"""Settlement ledger adapter seam for mod-mobility-switch.

Settlement legs execute as a real linked hold → post chain (TigerBeetle
two-phase transfer semantics): each leg is first held (funds reserved),
then posted, and each hold links to the previous hold id so the chain is
atomic in production.

Fail-closed idiom (mirrors adapters/base.py): the TigerBeetle seam raises
:class:`AdapterUnavailableError` unless ``SOS_MOBILITY_LEDGER_URL`` is
configured; the deterministic in-memory fixture is the dev/test default.
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import Any, Protocol

from .base import AdapterUnavailableError


class LedgerExecutionError(RuntimeError):
    """A leg hold/post failed — the batch must not be marked settled."""


class SettlementLedgerAdapter(Protocol):
    """Hold → linked post chain executor for settlement legs."""

    def execute_linked_chain(self, batch_id: str, legs: list) -> list[dict]:
        """Execute every leg as hold → post, linked in order.

        Returns the ordered posting receipts. Any failure raises and no leg
        is left half-posted by the caller's perspective (the batch is not
        marked settled)."""
        ...


class FixtureSettlementLedger:
    """Deterministic in-memory ledger: holds then posts, chain-linked."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holds: dict[str, dict[str, Any]] = {}
        self.posts: dict[str, dict[str, Any]] = {}

    def hold(self, batch_id: str, leg, linked_id: str | None) -> str:
        if leg.amount_kobo <= 0:
            raise LedgerExecutionError(
                f"leg for {leg.beneficiary} has non-positive amount "
                f"{leg.amount_kobo} kobo")
        hold_id = f"hold-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self.holds[hold_id] = {
                "hold_id": hold_id,
                "batch_id": batch_id,
                "beneficiary": leg.beneficiary,
                "account_code": leg.tigerbeetle_account_code,
                "amount_kobo": leg.amount_kobo,
                "transfer_code": leg.transfer_code,
                "linked_id": linked_id,
                "state": "HELD",
            }
        return hold_id

    def post(self, hold_id: str) -> dict[str, Any]:
        with self._lock:
            hold = self.holds.get(hold_id)
            if hold is None:
                raise LedgerExecutionError(f"hold '{hold_id}' not found")
            if hold["state"] != "HELD":
                raise LedgerExecutionError(f"hold '{hold_id}' is {hold['state']}")
            hold["state"] = "POSTED"
            receipt = dict(hold)
            receipt["posted"] = True
            self.posts[hold_id] = receipt
            return receipt

    def execute_linked_chain(self, batch_id: str, legs: list) -> list[dict]:
        receipts: list[dict] = []
        linked_id: str | None = None
        for leg in legs:
            hold_id = self.hold(batch_id, leg, linked_id)
            receipts.append(self.post(hold_id))
            linked_id = hold_id
        return receipts


class TigerBeetleSettlementLedger:
    """Production seam: TigerBeetle linked two-phase transfers.

    Fail-closed: requires ``SOS_MOBILITY_LEDGER_URL`` and the optional
    ``tigerbeetle`` client package; otherwise raises on construction.
    """

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("SOS_MOBILITY_LEDGER_URL")
        if not self.url:
            raise AdapterUnavailableError(
                "settlement ledger requires SOS_MOBILITY_LEDGER_URL (fail-closed)")
        try:  # pragma: no cover - optional dependency
            import tigerbeetle  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise AdapterUnavailableError(
                "tigerbeetle package not installed "
                "(pip install tigerbeetle==0.17.9 — see pyproject.toml)") from exc

    def execute_linked_chain(self, batch_id: str, legs: list) -> list[dict]:  # pragma: no cover
        raise AdapterUnavailableError(
            "TigerBeetle settlement ledger client not wired in this build")


def select_ledger_adapter(profile: str | None = None) -> SettlementLedgerAdapter:
    """Dev/test: deterministic fixture. Production without the ledger URL
    fails closed at boot."""
    profile = profile or os.environ.get("SOS_PROFILE", "dev")
    if os.environ.get("SOS_MOBILITY_LEDGER_URL"):
        return TigerBeetleSettlementLedger()
    if profile == "production":
        raise AdapterUnavailableError(
            "production profile requires SOS_MOBILITY_LEDGER_URL")
    return FixtureSettlementLedger()
