"""Saga: real compensations, descriptor-persisted recovery, FAILED alert."""
import json

import pytest

from ledger.fundsflow.outbox import InMemoryOutboxStore, OutboxWriter, RecordingEventBus
from ledger.fundsflow.saga import (
    SAGA_FAILED_TOPIC,
    AdapterUnavailableError,
    InMemorySagaStore,
    SagaCoordinator,
    SagaState,
    build_steps_from_descriptors,
    canonical_step_descriptors,
)
from ledger.fundsflow.tigerbeetle_flows import InMemoryTBClient

LEGS = [("crf", 3001, 65_000), ("mda", 2010, 35_000)]


def setup(descriptors=None):
    tb = InMemoryTBClient()
    tb.balances[1001] = 1_000_000   # payer collection account
    tb.balances[4001] = 500_000     # payout/escrow account
    outbox_store = InMemoryOutboxStore()
    bus = RecordingEventBus()
    writer = OutboxWriter(outbox_store)
    store = InMemorySagaStore()
    deps = {"tb_client": tb, "outbox_writer": writer}
    coord = SagaCoordinator(store, outbox_writer=writer, deps=deps)
    if descriptors is None:
        descriptors = canonical_step_descriptors(
            source_account=1001, legs=LEGS, payout=(4001, 9001, 50_000),
        )
    return coord, store, tb, writer, outbox_store, descriptors


def test_descriptors_are_json_serializable():
    descriptors = canonical_step_descriptors(
        source_account=1001, legs=LEGS, payout=(4001, 9001, 50_000)
    )
    json.dumps(descriptors)  # must not raise
    kinds = [d["kind"] for d in descriptors]
    assert kinds == [
        "tigerbeetle.hold", "tigerbeetle.split_commit",
        "tigerbeetle.payout", "outbox.event_publish",
    ]


def test_happy_path_moves_money_and_publishes():
    coord, store, tb, writer, outbox_store, descriptors = setup()
    rec = coord.begin("saga-ok", step_descriptors=descriptors)
    assert rec.context["step_descriptors"] == descriptors  # persisted at begin
    steps = build_steps_from_descriptors(descriptors, coord.deps)
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.COMPLETED
    assert tb.balance(3001) == 65_000 and tb.balance(2010) == 35_000
    assert tb.balance(9001) == 50_000
    assert len(outbox_store.outbox) == 1


def test_crash_after_split_commit_recovery_reverses_posted_legs():
    coord, store, tb, writer, outbox_store, descriptors = setup()
    rec = coord.begin("saga-crash", step_descriptors=descriptors)
    steps = build_steps_from_descriptors(descriptors, coord.deps)
    store.crash_on_save_state = SagaState.SPLIT_COMMITTED
    with pytest.raises(RuntimeError):
        coord.execute(rec, steps)  # process "dies" after split_commit
    store.crash_on_save_state = None

    # process restart: NEW coordinator over the SAME store, no old closures
    coord2 = SagaCoordinator(store, outbox_writer=writer,
                             deps={"tb_client": tb, "outbox_writer": writer})
    # payout fails downstream → compensation must reverse posted legs + more
    writer2 = writer
    # force payout failure by draining the payout account before recovery
    tb.balances[4001] = 0
    recovered = coord2.recover_unfinished()[0]
    assert recovered.state == SagaState.COMPENSATED
    # split_commit compensation reversed the POSTED legs (void is illegal)
    assert tb.balance(1001) == 1_000_000
    assert tb.balance(3001) == 0 and tb.balance(2010) == 0


def test_payout_failure_compensates_with_reversals_not_voids():
    coord, store, tb, writer, outbox_store, descriptors = setup()
    tb.balances[4001] = 10  # payout will fail (insufficient funds)
    rec = coord.begin("saga-payfail", step_descriptors=descriptors)
    steps = build_steps_from_descriptors(descriptors, coord.deps)
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.COMPENSATED
    # hold chain was posted then reversed; payout never happened
    assert tb.balance(1001) == 1_000_000
    assert tb.balance(3001) == 0 and tb.balance(2010) == 0 and tb.balance(9001) == 0
    # reversal transfers exist (posted), not voids of posted transfers
    reversals = [t for t in tb.transfers.values() if t.leg.endswith("|reversal")]
    assert len(reversals) == 2


def test_failed_state_emits_saga_failed_event():
    coord, store, tb, writer, outbox_store, descriptors = setup()
    rec = coord.begin("saga-failed", step_descriptors=descriptors)
    steps = build_steps_from_descriptors(descriptors, coord.deps)

    # event_publish fails → compensation walk starts; payout compensation is
    # broken → saga enters FAILED
    steps[3].action = lambda ctx: (_ for _ in ()).throw(RuntimeError("bus down"))

    def broken_payout_comp(ctx):
        raise RuntimeError("payout compensation broken")

    steps[2].compensate = broken_payout_comp
    rec = coord.execute(rec, steps)
    assert rec.state == SagaState.FAILED
    assert "compensation_error" in rec.context
    # FAILED alert written through the outbox
    assert len(outbox_store.outbox) == 1
    row = outbox_store.outbox[0]
    assert row.topic == SAGA_FAILED_TOPIC
    assert row.payload["saga_id"] == rec.saga_id
    assert row.payload["step"] == "payout"
    assert "payout compensation broken" in row.payload["error"]


def test_unregistered_descriptor_kind_fails_closed():
    with pytest.raises(AdapterUnavailableError):
        build_steps_from_descriptors(
            [{"kind": "mystery.step"}], {"tb_client": InMemoryTBClient()}
        )


def test_recovery_without_descriptors_or_steps_fails_closed():
    store = InMemorySagaStore()
    coord = SagaCoordinator(store)
    rec = coord.begin("saga-bare")  # no descriptors persisted
    with pytest.raises(AdapterUnavailableError):
        coord.recover_unfinished()
