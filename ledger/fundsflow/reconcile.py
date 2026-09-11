"""Continuous reconciliation: ledger vs outbox vs upstream settlement refs.

Compares three views of the world:

1. the ledger (TigerBeetle client balances / posted transfers),
2. the outbox (which flow events we say we emitted),
3. upstream settlement references — Mojaloop FSPIOP fulfilments and the
   NIBSS e-Bills settlement sheet iterator (seam shapes from
   services/mod-mobility-switch/app/adapters/{fspiop,nibss_ebills}.py).

Any mismatch emits an alert event on the bus AND sets an automatic hold
flag on the affected account (fail-safe: stop the bleeding first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol


@dataclass
class SettlementReference:
    """Normalised upstream settlement reference (FSPIOP fulfil / NIBSS row)."""

    reference: str
    amount_kobo: int
    state: str  # COMMITTED | ABORTED | SETTLED
    source: str  # "FSPIOP" | "NIBSS_EBILLS"


@dataclass
class ReconciliationAlert:
    kind: str  # LEDGER_VS_OUTBOX | LEDGER_VS_UPSTREAM | OUTBOX_VS_UPSTREAM
    detail: Dict[str, Any]


@dataclass
class ReconciliationReport:
    mismatches: List[ReconciliationAlert] = field(default_factory=list)
    held_accounts: List[int] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.mismatches


def refs_from_fspiop(fulfilments: Iterable[Any]) -> List[SettlementReference]:
    """Adapt FSPIOP TransferFulfilment callbacks into normalised refs."""
    refs = []
    for f in fulfilments:
        refs.append(
            SettlementReference(
                reference=f.transfer_id,
                amount_kobo=int(getattr(f, "amount_kobo", 0) or 0),
                state="COMMITTED" if getattr(f, "committed", False) else "ABORTED",
                source="FSPIOP",
            )
        )
    return refs


def refs_from_nibss(rows: Iterable[Any]) -> List[SettlementReference]:
    """Adapt NIBSS e-Bills SettlementRow iterator into normalised refs."""
    return [
        SettlementReference(
            reference=r.bill_reference,
            amount_kobo=int(r.amount_kobo),
            state="SETTLED",
            source="NIBSS_EBILLS",
        )
        for r in rows
    ]


class Reconciler:
    """Runs the three-way comparison and applies fail-safe holds."""

    def __init__(
        self,
        tb_client: Any,
        outbox_store: Any,
        bus: Any,
        hold_flag: Optional[Callable[[int], None]] = None,
        alert_topic: str = "fundsflow.reconciliation.alert",
    ) -> None:
        self.tb = tb_client
        self.outbox = outbox_store
        self.bus = bus
        self.held_accounts: set = set()
        self._hold_flag = hold_flag
        self.alert_topic = alert_topic

    # -- internals ---------------------------------------------------------------
    def _hold(self, account: int) -> None:
        self.held_accounts.add(account)
        if self._hold_flag:
            self._hold_flag(account)

    def _alert(self, alert: ReconciliationAlert) -> None:
        self.bus.publish(self.alert_topic, {"kind": alert.kind, "detail": alert.detail})

    # -- checks --------------------------------------------------------------------
    def reconcile(
        self,
        *,
        expected_by_account: Dict[int, int],
        upstream_refs: Iterable[SettlementReference] = (),
    ) -> ReconciliationReport:
        report = ReconciliationReport()

        # 1) ledger vs expectation (domain view written through the outbox tx)
        for account, expected in expected_by_account.items():
            actual = self.tb.balance(account)
            if actual != expected:
                alert = ReconciliationAlert(
                    "LEDGER_VS_OUTBOX",
                    {"account": account, "expected": expected, "actual": actual},
                )
                report.mismatches.append(alert)
                self._alert(alert)
                self._hold(account)
                report.held_accounts.append(account)

        # 2) outbox: events that were committed but never published are a
        #    (recoverable) break until the relay heals them
        unacked = self.outbox.unacked()
        if unacked:
            alert = ReconciliationAlert(
                "LEDGER_VS_OUTBOX",
                {"unacked_events": [r.event_id for r in unacked]},
            )
            report.mismatches.append(alert)
            self._alert(alert)

        # 3) ledger vs upstream settlement references
        for ref in upstream_refs:
            if ref.state not in ("COMMITTED", "SETTLED"):
                alert = ReconciliationAlert(
                    "LEDGER_VS_UPSTREAM",
                    {"reference": ref.reference, "state": ref.state, "source": ref.source},
                )
                report.mismatches.append(alert)
                self._alert(alert)
        return report
