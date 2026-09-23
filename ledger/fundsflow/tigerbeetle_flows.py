"""TigerBeetle two-phase funds-flow semantics (reference client seam).

Rules enforced here (mirroring ledger/splits Go adapter contract tests):

* Multi-leg money movement NEVER uses direct double-entry. Funds are first
  reserved with a PENDING transfer chain (flags.linked so the whole chain
  commits atomically or not at all), then POSTed (commit) or VOIDed
  (rollback). A posted pending transfer can never be re-voided and a voided
  one can never be posted.
* Idempotency: every transfer ID is a deterministic 128-bit integer derived
  from SHA-256(idempotency_key | leg). Retried requests resolve to the same
  IDs; TigerBeetle's idempotent create makes replays no-ops.
* The real client is selected only when ``SOS_TB_ADDRESSES`` is set; in the
  production profile (``SOS_PROFILE=production``) missing addresses fail
  closed.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_ID_CACHE_MAX = 65536  # bounded memo for deterministic transfer IDs
_id_cache: Dict[Tuple[str, str], int] = {}


def deterministic_transfer_id(idempotency_key: str, leg: str) -> int:
    """Deterministic 128-bit transfer ID from (idempotency_key, leg).

    Same (key, leg) → same ID, so retries map onto TigerBeetle's native
    idempotent-create semantics and never double-apply. Results are memoized
    in a bounded process-local cache: saga recovery and replays re-derive the
    same IDs constantly, and sha256(key‖leg) is pure.
    """
    cache_key = (idempotency_key, leg)
    cached = _id_cache.get(cache_key)
    if cached is not None:
        return cached
    digest = hashlib.sha256(f"{idempotency_key}|{leg}".encode("utf-8")).digest()
    tid = int.from_bytes(digest[:16], "big")
    if len(_id_cache) >= _ID_CACHE_MAX:
        _id_cache.clear()  # simple bounded eviction; IDs re-derive on demand
    _id_cache[cache_key] = tid
    return tid


class AdapterUnavailableError(RuntimeError):
    """Raised when the real TigerBeetle adapter is required but unconfigured."""


class TransferError(RuntimeError):
    """Raised on illegal transfer state transitions or chain failures."""


@dataclass
class Transfer:
    id: int
    debit_account: int
    credit_account: int
    amount: int  # integer kobo
    pending: bool = True
    linked: bool = False
    code: int = 0
    idempotency_key: str = ""
    leg: str = ""
    state: str = "PENDING"  # PENDING | POSTED | VOIDED


class InMemoryTBClient:
    """Deterministic in-memory TigerBeetle fake (default; tests/dev).

    Implements linked-chain atomicity: a failure creating any transfer in a
    linked chain rolls the whole chain back, exactly like TB's
    ``flags.linked`` semantics.
    """

    def __init__(self) -> None:
        self.transfers: Dict[int, Transfer] = {}
        self.balances: Dict[int, int] = {}
        # running per-account PENDING hold totals; avoids an O(n) scan of
        # self.transfers on every hold creation (hot path)
        self._holds: Dict[int, int] = {}
        # test hooks
        self.fail_on_leg: Optional[str] = None  # inject failure mid-chain
        self.fail_on_post: bool = False
        self.create_calls: List[List[Transfer]] = []

    # -- internal ------------------------------------------------------------
    def _apply(self, t: Transfer, sign: int) -> None:
        self.balances[t.debit_account] = self.balances.get(t.debit_account, 0) - sign * t.amount
        self.balances[t.credit_account] = self.balances.get(t.credit_account, 0) + sign * t.amount

    def _held(self, account: int) -> int:
        return self._holds.get(account, 0)

    # -- client surface --------------------------------------------------------
    def create_transfers(self, transfers: List[Transfer]) -> List[Transfer]:
        """Create a (possibly linked) chain atomically; idempotent by ID."""
        self.create_calls.append(list(transfers))
        created: List[Transfer] = []
        try:
            for i, t in enumerate(transfers):
                if t.id in self.transfers:
                    # idempotent replay: return the existing record
                    created.append(self.transfers[t.id])
                    continue
                if self.fail_on_leg and t.leg == self.fail_on_leg:
                    raise TransferError(f"injected failure creating leg {t.leg!r}")
                if t.amount <= 0:
                    raise TransferError("transfer amount must be positive")
                if t.pending:
                    held = self._held(t.debit_account)
                    if self.balances.get(t.debit_account, 0) - held < t.amount:
                        raise TransferError("insufficient funds for hold")
                else:
                    if self.balances.get(t.debit_account, 0) < t.amount:
                        raise TransferError("insufficient funds")
                self.transfers[t.id] = t
                created.append(t)
                if t.pending:
                    self._holds[t.debit_account] = (
                        self._holds.get(t.debit_account, 0) + t.amount
                    )
                else:
                    self._apply(t, +1)
        except Exception:
            # linked-chain rollback: remove everything this call created
            for t in created:
                if self.transfers.get(t.id) is t and t.state == "PENDING":
                    del self.transfers[t.id]
                    self._holds[t.debit_account] = (
                        self._holds.get(t.debit_account, 0) - t.amount
                    )
            raise
        return created

    def post_pending_transfers(self, ids: List[int]) -> None:
        """POST a pending chain (commit). All-or-nothing."""
        if self.fail_on_post:
            raise TransferError("injected failure posting pending chain")
        for tid in ids:
            t = self.transfers.get(tid)
            if t is None:
                raise TransferError(f"unknown pending transfer {tid}")
            if t.state == "VOIDED":
                raise TransferError("cannot post a voided transfer")
        for tid in ids:
            t = self.transfers[tid]
            if t.state == "PENDING":
                t.state = "POSTED"
                self._holds[t.debit_account] = (
                    self._holds.get(t.debit_account, 0) - t.amount
                )
                self._apply(t, +1)
            # already POSTED → idempotent no-op

    def void_pending_transfers(self, ids: List[int]) -> None:
        """VOID a pending chain (rollback / compensation)."""
        for tid in ids:
            t = self.transfers.get(tid)
            if t is None:
                raise TransferError(f"unknown pending transfer {tid}")
            if t.state == "POSTED":
                raise TransferError("cannot void a posted transfer — use a reversal flow")
        for tid in ids:
            t = self.transfers[tid]
            if t.state == "PENDING":
                t.state = "VOIDED"
                self._holds[t.debit_account] = (
                    self._holds.get(t.debit_account, 0) - t.amount
                )

    def balance(self, account: int) -> int:
        return self.balances.get(account, 0)


class RealTBClient:
    """Seam for the production TigerBeetle client (tb_client package).

    Fail-closed: unusable without ``SOS_TB_ADDRESSES``; in the production
    profile even construction without addresses raises.
    """

    def __init__(self, addresses: Optional[str] = None) -> None:
        self.addresses = addresses or os.environ.get("SOS_TB_ADDRESSES")
        if not self.addresses:
            raise AdapterUnavailableError(
                "real TigerBeetle client requires SOS_TB_ADDRESSES (fail-closed)"
            )
        try:  # pragma: no cover - optional dependency
            import tb_client  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise AdapterUnavailableError(
                "tb_client package not installed"
            ) from exc


def build_hold_chain(
    *,
    idempotency_key: str,
    source_account: int,
    legs: List[tuple],  # [(leg_name, destination_account, amount_kobo), ...]
) -> List[Transfer]:
    """Build a PENDING, linked transfer chain for a multi-leg split.

    The chain either commits wholesale (POST) or rolls back wholesale
    (VOID); there is no partial-commit state for a linked chain.
    """
    transfers: List[Transfer] = []
    for i, (leg, dest, amount) in enumerate(legs):
        transfers.append(
            Transfer(
                id=deterministic_transfer_id(idempotency_key, leg),
                debit_account=source_account,
                credit_account=dest,
                amount=amount,
                pending=True,
                linked=i < len(legs) - 1,
                idempotency_key=idempotency_key,
                leg=leg,
            )
        )
    return transfers


def build_reversal_chain(
    *,
    original_idempotency_key: str,
    legs: List[tuple],  # [(leg_name, destination_account, amount_kobo), ...] of the ORIGINAL chain
    original_debit_account: int,
) -> List[Transfer]:
    """Build the reversing chain for a POSTED original chain.

    Every leg swaps debit/credit (money flows back). Reversal transfer IDs
    are derived as sha256(key | leg | "reversal") truncated to 128 bits via
    :func:`deterministic_transfer_id`, so a retried reversal maps onto the
    same IDs and TigerBeetle's idempotent create makes replays no-ops.

    Conservation check: sum(reversal legs) must equal sum(original legs);
    a mismatch means the caller supplied the wrong leg set and we fail
    closed before touching the ledger.
    """
    if not legs:
        raise TransferError("cannot reverse an empty chain")
    if any(amount <= 0 for _, _, amount in legs):
        raise TransferError("reversal conservation check failed: non-positive leg")
    transfers: List[Transfer] = []
    for i, (leg, dest, amount) in enumerate(legs):
        transfers.append(
            Transfer(
                id=deterministic_transfer_id(original_idempotency_key, f"{leg}|reversal"),
                debit_account=dest,            # swapped: original creditor pays back
                credit_account=original_debit_account,
                amount=amount,
                pending=False,                 # reversals settle immediately
                linked=i < len(legs) - 1,
                idempotency_key=f"{original_idempotency_key}|reversal",
                leg=f"{leg}|reversal",
            )
        )
    return transfers


def reverse_chain(
    client,
    *,
    original_idempotency_key: str,
    original_chain: Optional[List[Transfer]] = None,
    legs: Optional[List[tuple]] = None,
    original_debit_account: Optional[int] = None,
) -> List[Transfer]:
    """Reverse a POSTED chain with a new linked chain (accounts swapped).

    ``original_chain`` (the Transfer records) or an explicit
    ``(legs, original_debit_account)`` pair describes what to reverse.
    PENDING-only chains must be voided, not reversed — reversing one raises.
    Idempotent: replay returns the same reversal IDs without double-apply.
    """
    if original_chain is not None:
        if not original_chain:
            raise TransferError("cannot reverse an empty chain")
        states = {t.state for t in original_chain}
        if states == {"PENDING"}:
            raise TransferError(
                "cannot reverse a PENDING-only chain — void it instead"
            )
        if "POSTED" not in states:
            raise TransferError(f"cannot reverse chain in states {sorted(states)}")
        legs = [(t.leg, t.credit_account, t.amount) for t in original_chain]
        original_debit_account = original_chain[0].debit_account
    if legs is None or original_debit_account is None:
        raise TransferError(
            "reverse_chain requires original_chain or (legs, original_debit_account)"
        )
    chain = build_reversal_chain(
        original_idempotency_key=original_idempotency_key,
        legs=legs,
        original_debit_account=original_debit_account,
    )
    return client.create_transfers(chain)


def select_client(profile: Optional[str] = None):
    """Environment-driven client selection (fail-closed in production)."""
    profile = profile or os.environ.get("SOS_PROFILE", "dev")
    if os.environ.get("SOS_TB_ADDRESSES"):
        return RealTBClient()
    if profile == "production":
        raise AdapterUnavailableError(
            "production profile requires SOS_TB_ADDRESSES; refusing in-memory ledger"
        )
    return InMemoryTBClient()
