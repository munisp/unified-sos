"""Transparency sources: where the public projections are read from.

``InMemoryTransparencySource`` is the deterministic default used for local
runs and tests (fixture data mirroring mod-police-cad / mod-ppp-investment
shapes). ``HttpTransparencySource`` is the production seam: it reads the
source modules' own public audit surfaces over HTTP and is *fail-closed* —
constructed only when both upstream base URLs are configured, and any
upstream error surfaces as ``SourceUnavailableError`` (HTTP 503) rather
than silently serving stale or fabricated transparency data
(cf. mod-kyc-kyb app/adapters/base.py fail-closed idiom).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol

from .domain import (
    AUDIT_GENESIS_HASH,
    KNOWN_STATE_IDS,
    ConcessionEscrowStatement,
    ProcurementAuditDigest,
    TrustFundEntry,
    TrustFundEntryKind,
    TrustFundFeed,
    compute_entry_hash,
    sha256_hex,
)


class SourceUnavailableError(RuntimeError):
    """Raised when a configured upstream transparency source is unreachable."""


class TransparencySource(Protocol):
    """Read-only projection source; every method is tenant-scoped."""

    def trust_fund_feed(self, state_id: str) -> TrustFundFeed: ...
    def escrow_statements(self, state_id: str) -> list[ConcessionEscrowStatement]: ...
    def procurement_audit(self, state_id: str) -> list[ProcurementAuditDigest]: ...


def _trust_cursor(prev: str, entry_id: str, kind: str, amount_kobo: int, occurred_at: str) -> str:
    return compute_entry_hash(prev, f"{entry_id}|{kind}|{amount_kobo}|{occurred_at}")


def _statement_cursor(prev: str, statement_id: str, period: str, gross_kobo: int) -> str:
    return compute_entry_hash(prev, f"{statement_id}|{period}|{gross_kobo}")


class InMemoryTransparencySource:
    """Deterministic fixture source for local development and tests.

    Data mirrors the public shapes of mod-police-cad's
    ``GET /cad/v1/trust-fund/{state}/audit-feed`` and mod-ppp-investment's
    settlement statements + audit chain, pre-redacted at the boundary.
    """

    #: (kind, amount_kobo, alias_seed/purpose, occurred_at) per entry.
    _TRUST_FIXTURES: dict[str, list[tuple[str, int, str, str]]] = {
        "lagos": [
            ("donation", 50_000_000_00, "donor-ref-alpha", "2026-01-05T09:00:00+00:00"),
            ("donation", 12_500_000_00, "donor-ref-beta", "2026-01-12T09:00:00+00:00"),
            ("disbursement", 20_000_000_00, "Patrol vehicle maintenance", "2026-02-01T09:00:00+00:00"),
        ],
        "ogun": [
            ("donation", 8_000_000_00, "donor-ref-gamma", "2026-01-08T09:00:00+00:00"),
        ],
        "osun": [],
        "benue": [],
        "nasarawa": [
            ("donation", 5_000_000_00, "donor-ref-delta", "2026-01-15T09:00:00+00:00"),
        ],
        "taraba": [],
    }

    #: (contract_ref, period, gross, state_share, concessionaire_share, bps, reconciled)
    _ESCROW_FIXTURES: dict[str, list[tuple[str, str, int, int, int, int, bool]]] = {
        "nasarawa": [
            ("ctr-nas-000001", "2026-01", 40_000_000_00, 6_000_000_00, 34_000_000_00, 1500, True),
        ],
        "taraba": [
            ("ctr-tar-000001", "2026-01", 12_000_000_00, 1_800_000_00, 10_200_000_00, 1500, True),
        ],
        "lagos": [],
        "ogun": [],
        "osun": [],
        "benue": [],
    }

    #: (action, subject_ref) pairs chained from AUDIT_GENESIS_HASH per state.
    _AUDIT_FIXTURES: dict[str, list[tuple[str, str]]] = {
        "nasarawa": [
            ("PROJECT_CREATED", "proj-nas-000001"),
            ("PROJECT_PUBLISHED", "proj-nas-000001"),
            ("PROPOSAL_AWARDED", "prop-nas-000001"),
        ],
        "taraba": [
            ("PROJECT_CREATED", "proj-tar-000001"),
            ("PROJECT_PUBLISHED", "proj-tar-000001"),
        ],
        "lagos": [],
        "ogun": [],
        "osun": [],
        "benue": [],
    }

    def trust_fund_feed(self, state_id: str) -> TrustFundFeed:
        entries: list[TrustFundEntry] = []
        prev = AUDIT_GENESIS_HASH
        balance = 0
        for i, (kind, amount, ref, at) in enumerate(self._TRUST_FIXTURES.get(state_id, []), start=1):
            cursor = _trust_cursor(prev, f"tf-{state_id}-{i:04d}", kind, amount, at)
            entries.append(TrustFundEntry(
                entry_id=f"tf-{state_id}-{i:04d}",
                kind=TrustFundEntryKind(kind),
                amount_kobo=amount,
                donor_alias_hash=sha256_hex(f"donor:{state_id}:{ref}") if kind == "donation" else None,
                purpose_label=ref if kind == "disbursement" else None,
                occurred_at=at,
                cursor=cursor,
            ))
            balance += amount if kind == "donation" else -amount
            prev = cursor
        return TrustFundFeed(state_id=state_id, balance_kobo=balance,
                             entries=entries, head_cursor=prev)

    def escrow_statements(self, state_id: str) -> list[ConcessionEscrowStatement]:
        out: list[ConcessionEscrowStatement] = []
        prev = AUDIT_GENESIS_HASH
        for i, (cref, period, gross, state_share, con_share, bps, rec) in enumerate(
                self._ESCROW_FIXTURES.get(state_id, []), start=1):
            sid = f"esc-{state_id}-{i:04d}"
            cursor = _statement_cursor(prev, sid, period, gross)
            out.append(ConcessionEscrowStatement(
                statement_id=sid,
                contract_ref_hash=sha256_hex(f"contract:{state_id}:{cref}"),
                period=period,
                gross_collections_kobo=gross,
                state_share_kobo=state_share,
                concessionaire_share_kobo=con_share,
                applied_state_share_bps=bps,
                reconciled=rec,
                cursor=cursor,
            ))
            prev = cursor
        return out

    def procurement_audit(self, state_id: str) -> list[ProcurementAuditDigest]:
        from datetime import datetime, timezone

        out: list[ProcurementAuditDigest] = []
        prev = AUDIT_GENESIS_HASH
        for seq, (action, subject) in enumerate(self._AUDIT_FIXTURES.get(state_id, []), start=1):
            subject_hash = sha256_hex(f"subject:{state_id}:{subject}")
            payload = f"{seq}|{action}|{subject_hash}"
            entry_hash = compute_entry_hash(prev, payload)
            out.append(ProcurementAuditDigest(
                seq=seq, action=action, subject_ref_hash=subject_hash,
                prev_hash=prev, entry_hash=entry_hash,
                at=datetime(2026, 1, seq, tzinfo=timezone.utc),
            ))
            prev = entry_hash
        return out


class HttpTransparencySource:
    """Production seam: reads the source modules' public audit surfaces.

    Fail-closed: construction requires both upstream base URLs; requests are
    unauthenticated (the upstream feeds are public) and any network/HTTP
    error raises ``SourceUnavailableError``.
    """

    def __init__(self, police_cad_base_url: str, ppp_investment_base_url: str,
                 timeout_seconds: float = 5.0) -> None:
        if not police_cad_base_url or not ppp_investment_base_url:
            raise SourceUnavailableError(
                "HttpTransparencySource requires POLICE_CAD_BASE_URL and "
                "PPP_INVESTMENT_BASE_URL (fail-closed: no upstream, no transparency data)"
            )
        self._cad = police_cad_base_url.rstrip("/")
        self._ppp = ppp_investment_base_url.rstrip("/")
        self._timeout = timeout_seconds

    @classmethod
    def from_env(cls) -> "HttpTransparencySource":
        return cls(
            police_cad_base_url=os.environ.get("POLICE_CAD_BASE_URL", ""),
            ppp_investment_base_url=os.environ.get("PPP_INVESTMENT_BASE_URL", ""),
            timeout_seconds=float(os.environ.get("TRANSPARENCY_UPSTREAM_TIMEOUT", "5")),
        )

    def _get_json(self, url: str):
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceUnavailableError(f"upstream transparency source failed: {url}") from exc

    def trust_fund_feed(self, state_id: str) -> TrustFundFeed:
        """Projects mod-police-cad ``/cad/v1/trust-fund/{state}/audit-feed``,
        redacting donor_ref to a one-way pseudonym and adding cursors."""
        raw = self._get_json(f"{self._cad}/cad/v1/trust-fund/{state_id}/audit-feed")
        entries: list[TrustFundEntry] = []
        prev = AUDIT_GENESIS_HASH
        i = 0
        for kind, items in (("donation", raw.get("donations", [])),
                            ("disbursement", raw.get("disbursements", []))):
            for item in items:
                i += 1
                entry_id = item.get("donation_id") or item.get("disbursement_id") or f"tf-{state_id}-{i:04d}"
                amount = int(item["amount_kobo"])
                at = item.get("received_at") or item.get("disbursed_at") or ""
                cursor = _trust_cursor(prev, entry_id, kind, amount, at)
                entries.append(TrustFundEntry(
                    entry_id=entry_id,
                    kind=TrustFundEntryKind(kind),
                    amount_kobo=amount,
                    donor_alias_hash=(sha256_hex(f"donor:{state_id}:{item['donor_ref']}")
                                      if kind == "donation" else None),
                    purpose_label=item.get("purpose") if kind == "disbursement" else None,
                    occurred_at=at,
                    cursor=cursor,
                ))
                prev = cursor
        return TrustFundFeed(state_id=state_id, balance_kobo=int(raw.get("balance_kobo", 0)),
                             entries=entries, head_cursor=prev)

    def escrow_statements(self, state_id: str) -> list[ConcessionEscrowStatement]:
        """mod-ppp-investment has no public statement list endpoint; the
        production wiring exposes one and this seam consumes it."""
        raw = self._get_json(f"{self._ppp}/transparency/escrow-statements?state_id={state_id}")
        out: list[ConcessionEscrowStatement] = []
        prev = AUDIT_GENESIS_HASH
        for item in raw.get("statements", []):
            sid = item["statement_id"]
            gross = int(item["gross_collections_kobo"])
            cursor = _statement_cursor(prev, sid, item["period"], gross)
            out.append(ConcessionEscrowStatement(
                statement_id=sid,
                contract_ref_hash=sha256_hex(f"contract:{state_id}:{item['contract_id']}"),
                period=item["period"],
                gross_collections_kobo=gross,
                state_share_kobo=int(item["state_share_kobo"]),
                concessionaire_share_kobo=int(item["concessionaire_share_kobo"]),
                applied_state_share_bps=int(item["applied_state_share_bps"]),
                reconciled=bool(item["reconciled"]),
                cursor=cursor,
            ))
            prev = cursor
        return out

    def procurement_audit(self, state_id: str) -> list[ProcurementAuditDigest]:
        """Redacted projection of mod-ppp-investment ``/audit?state_id=...``;
        actor_id and details are dropped, subject_id pseudonymised, and the
        digest chain is re-anchored so verification does not need PII."""
        raw = self._get_json(f"{self._ppp}/audit?state_id={state_id}")
        out: list[ProcurementAuditDigest] = []
        prev = AUDIT_GENESIS_HASH
        for seq, item in enumerate(raw, start=1):
            subject_hash = sha256_hex(f"subject:{state_id}:{item['subject_id']}")
            payload = f"{seq}|{item['action']}|{subject_hash}"
            entry_hash = compute_entry_hash(prev, payload)
            out.append(ProcurementAuditDigest(
                seq=seq, action=item["action"], subject_ref_hash=subject_hash,
                prev_hash=prev, entry_hash=entry_hash, at=item["at"],
            ))
            prev = entry_hash
        return out


def default_source() -> TransparencySource:
    """Fail-closed default wiring: HTTP seam when both upstreams are
    configured, otherwise the deterministic in-memory fixture (local dev)."""
    if os.environ.get("POLICE_CAD_BASE_URL") and os.environ.get("PPP_INVESTMENT_BASE_URL"):
        return HttpTransparencySource.from_env()
    return InMemoryTransparencySource()
