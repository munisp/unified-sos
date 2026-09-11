"""Reconciliation alerts/holds and workflow retry+compensation semantics."""
import pytest

from ledger.fundsflow.outbox import InMemoryOutboxStore, OutboxWriter, RecordingEventBus
from ledger.fundsflow.reconcile import (
    Reconciler,
    SettlementReference,
    refs_from_nibss,
)
from ledger.fundsflow.tigerbeetle_flows import InMemoryTBClient
from ledger.fundsflow.workflows import (
    FundsTransferWorkflow,
    RetryPolicy,
    SettlementReconciliationWorkflow,
    WorkflowFailure,
)


def make_reconciler():
    tb = InMemoryTBClient()
    store = InMemoryOutboxStore()
    bus = RecordingEventBus()
    return tb, store, bus, Reconciler(tb, store, bus)


def test_clean_reconciliation_reports_nothing():
    tb, store, bus, rec = make_reconciler()
    tb.balances[3001] = 65_000
    report = rec.reconcile(expected_by_account={3001: 65_000})
    assert report.clean and bus.published == []


def test_ledger_mismatch_alerts_and_holds_account():
    tb, store, bus, rec = make_reconciler()
    tb.balances[3001] = 64_999  # 1 kobo missing
    report = rec.reconcile(expected_by_account={3001: 65_000})
    assert not report.clean
    assert 3001 in report.held_accounts and 3001 in rec.held_accounts
    assert bus.published[0][0] == "fundsflow.reconciliation.alert"


def test_unacked_outbox_rows_raise_alert():
    tb, store, bus, rec = make_reconciler()
    OutboxWriter(store).write(domain_record={}, topic="t", event_payload={},
                              idempotency_key="k")
    report = rec.reconcile(expected_by_account={})
    assert any(m.kind == "LEDGER_VS_OUTBOX" for m in report.mismatches)


def test_aborted_upstream_fulfilment_alerts():
    tb, store, bus, rec = make_reconciler()
    refs = [SettlementReference("t-1", 500, "ABORTED", "FSPIOP")]
    report = rec.reconcile(expected_by_account={}, upstream_refs=refs)
    assert any(m.kind == "LEDGER_VS_UPSTREAM" for m in report.mismatches)


def test_refs_from_nibss_iterator():
    class Row:
        bill_reference = "B1"
        amount_kobo = 700
    refs = refs_from_nibss([Row()])
    assert refs[0].state == "SETTLED" and refs[0].amount_kobo == 700


# --- workflows ---------------------------------------------------------------

def test_workflow_happy_path():
    order = []
    wf = FundsTransferWorkflow(
        hold=lambda: order.append("hold"),
        split_commit=lambda: order.append("split"),
        publish=lambda: order.append("publish"),
        settle=lambda: order.append("settle"),
    )
    result = wf.run()
    assert result["status"] == "COMPLETED"
    assert order == ["hold", "split", "publish", "settle"]


def test_workflow_retries_with_backoff_then_succeeds():
    attempts = {"n": 0}
    sleeps = []

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")

    wf = FundsTransferWorkflow(hold=flaky, retry=RetryPolicy(max_attempts=5))
    import ledger.fundsflow.workflows as w
    orig_sleep = w.time.sleep
    w.time.sleep = lambda s: sleeps.append(s)
    try:
        result = wf.run()
    finally:
        w.time.sleep = orig_sleep
    assert result["status"] == "COMPLETED"
    assert sleeps and sleeps == sorted(sleeps)  # exponential backoff, increasing


def test_workflow_failure_compensates_completed_money_steps():
    undone = []

    def bad_split():
        raise RuntimeError("permanent")

    wf = FundsTransferWorkflow(
        hold=lambda: None,
        split_commit=bad_split,
        compensate_hold=lambda: undone.append("void_hold"),
        retry=RetryPolicy(max_attempts=2, initial_interval=0),
    )
    result = wf.run()
    assert result["status"] == "COMPENSATED"
    assert undone == ["void_hold"]


def test_settlement_reconciliation_workflow_runs_sweep():
    calls = []
    wf = SettlementReconciliationWorkflow(reconcile=lambda: calls.append(1) or "ok")
    assert wf.run() == "ok" and calls == [1]
