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
from typing import Dict, List, Optional


class AdapterUnavailableError(RuntimeError):
    """Raised when the real TigerBeetle adapter is required but unconfigured."""


class TransferError(RuntimeError):
    """Raised on illegal transfer state transitions or chain failures."""


def deterministic_transfer_id(idempotency_key: str, leg: str) -> int:
    """Deterministic 128-bit transfer ID from (idempotency_key, leg).

    Same (key, leg) → same ID, so retries map onto TigerBeetle's native
    idempotent-create semantics and never double-apply.
    """
    digest = hashlib.sha256(f"{idempotency_key}|{leg}".encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


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
        # test hooks
        self.fail_on_leg: Optional[str] = None  # inject failure mid-chain
        self.fail_on_post: bool = False
        self.create_calls: List[List[Transfer]] = []

    # -- internal ------------------------------------------------------------
    def _apply(self, t: Transfer, sign: int) -> None:
        self.balances[t.debit_account] = self.balances.get(t.debit_account, 0) - sign * t.amount
        self.balances[t.credit_account] = self.balances.get(t.credit_account, 0) + sign * t.amount

    def _held(self, account: int) -> int:
        return sum(
            t.amount
            for t in self.transfers.values()
            if t.state == "PENDING" and t.debit_account == account
        )

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
                if not t.pending:
                    self._apply(t, +1)
        except Exception:
            # linked-chain rollback: remove everything this call created
            for t in created:
                if self.transfers.get(t.id) is t and t.state == "PENDING":
                    del self.transfers[t.id]
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
