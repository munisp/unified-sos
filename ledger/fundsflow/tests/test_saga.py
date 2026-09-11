"""Saga coordinator: happy path, compensation, crash recovery, seams."""
import pytest

from ledger.fundsflow.saga import (
    AdapterUnavailableError,
    InMemorySagaStore,
    PostgresSagaStore,
    SagaCoordinator,
    SagaState,
    SagaStep,
)


def make_steps(fail_at=None, log=None):
    log = log if log is not None else []

    def action(name):
        def run(ctx):
            log.append(f"do:{name}")
            if fail_at == name:
                raise RuntimeError(f"boom at {name}")
        return run

    def comp(name):
        def run(ctx):
            log.append(f"undo:{name}")
        return run

    return [
        SagaStep("hold", action("hold"), comp("hold"), SagaState.HELD),
        SagaStep("split_commit", action("split_commit"), comp("split_commit"),
                 SagaState.SPLIT_COMMITTED),
        SagaStep("payout", action("payout"), comp("payout"), SagaState.PAYOUT_DONE),
        SagaStep("event_publish", action("event_publish"), None,
                 SagaState.EVENT_PUBLISHED),
    ], log


def test_happy_path_reaches_completed():
    coord = SagaCoordinator()
    rec = coord.begin("flow-1")
    steps, log = make_steps()
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.COMPLETED
    assert rec.completed_steps == ["hold", "split_commit", "payout", "event_publish"]


def test_idempotent_begin_returns_same_saga():
    coord = SagaCoordinator()
    a = coord.begin("flow-dup")
    b = coord.begin("flow-dup")
    assert a.saga_id == b.saga_id


def test_failure_at_split_commit_compensates_hold():
    coord = SagaCoordinator()
    rec = coord.begin("flow-2")
    steps, log = make_steps(fail_at="split_commit")
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.COMPENSATED
    assert log == ["do:hold", "do:split_commit", "undo:hold"]


def test_failure_at_payout_compensates_in_reverse_order():
    coord = SagaCoordinator()
    rec = coord.begin("flow-3")
    steps, log = make_steps(fail_at="payout")
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.COMPENSATED
    assert log[-2:] == ["undo:split_commit", "undo:hold"]


def test_event_publish_failure_still_compensates_money_steps():
    coord = SagaCoordinator()
    rec = coord.begin("flow-4")
    steps, log = make_steps(fail_at="event_publish")
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.COMPENSATED
    assert "undo:payout" in log and "undo:hold" in log


def test_compensation_failure_marks_failed_for_operator():
    coord = SagaCoordinator()
    rec = coord.begin("flow-5")

    def bad_comp(ctx):
        raise RuntimeError("compensation broken")

    steps = [
        SagaStep("hold", lambda ctx: None, bad_comp, SagaState.HELD),
        SagaStep("split_commit", lambda ctx: (_ for _ in ()).throw(RuntimeError("x")),
                 None, SagaState.SPLIT_COMMITTED),
    ]
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.FAILED
    assert "compensation_error" in rec.context


def test_crash_after_hold_then_recovery_completes_without_double_hold():
    store = InMemorySagaStore()
    coord = SagaCoordinator(store)
    rec = coord.begin("flow-6")
    steps, log = make_steps()
    store.crash_on_save_state = SagaState.HELD
    with pytest.raises(RuntimeError):
        coord.execute(rec, steps)  # process "dies" right after hold
    store.crash_on_save_state = None
    recovered = coord.recover_unfinished(steps)
    assert recovered[0].state == SagaState.COMPLETED
    assert log.count("do:hold") == 1  # hold not re-executed (resume)


def test_postgres_seam_fail_closed_without_dsn(monkeypatch):
    monkeypatch.delenv("SOS_FUNDSFLOW_DSN", raising=False)
    with pytest.raises(AdapterUnavailableError):
        PostgresSagaStore()
