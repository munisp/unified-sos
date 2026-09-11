"""End-to-end crash-injection: conservation of value must hold in ALL cases.

Full flow: payment received (1001) → statutory split legs → concessionaire
escrow (2099) → settlement payout, coordinated by saga + TB two-phase +
outbox + idempotency. We inject crashes at every seam and assert:

* no partial money movement (linked chain atomicity)
* no double credit (deterministic IDs + idempotency middleware)
* no lost events (outbox recovery scan)
"""
import pytest

from ledger.fundsflow.idempotency import IdempotencyConflict, IdempotencyMiddleware
from ledger.fundsflow.invariants import ConservationEnforcer
from ledger.fundsflow.outbox import (
    InMemoryOutboxStore,
    OutboxRelay,
    OutboxWriter,
    RecordingEventBus,
)
from ledger.fundsflow.saga import InMemorySagaStore, SagaCoordinator, SagaState, SagaStep
from ledger.fundsflow.tigerbeetle_flows import InMemoryTBClient, build_hold_chain

SOURCE = 1001
SPLIT_PCTS = [
    ("STATE_CONSOLIDATED_REVENUE_FUND", 3001, 65.0),
    ("MDA_RETENTION_ACCOUNT", 2010, 15.0),
    ("PPP_TECH_CONCESSIONAIRE_ESCROW", 2099, 10.0),
    ("LOCAL_GOVERNMENT_SHARE_POOL", 2020, 5.0),
    ("SECURITY_TRUST_FUND", 4001, 5.0),
]
AMOUNT = 100_001


class Flow:
    """Composed funds flow over all middleware pieces."""

    def __init__(self):
        self.tb = InMemoryTBClient()
        self.tb.balances[SOURCE] = 10_000_000
        self.enforcer = ConservationEnforcer()
        self.saga_store = InMemorySagaStore()
        self.coord = SagaCoordinator(self.saga_store)
        self.outbox_store = InMemoryOutboxStore()
        self.bus = RecordingEventBus()
        self.relay = OutboxRelay(self.outbox_store, self.bus)
        self.idem = IdempotencyMiddleware()

    def steps_for(self, key):
        legs = self.enforcer.compute_split(AMOUNT, SPLIT_PCTS)
        tb, outbox, relay = self.tb, self.outbox_store, self.relay
        chain = build_hold_chain(
            idempotency_key=key,
            source_account=SOURCE,
            legs=[(b, a, amt) for b, a, amt in legs],
        )
        ids = [t.id for t in chain]

        def hold(ctx):
            tb.create_transfers(chain)

        def undo_hold(ctx):
            tb.void_pending_transfers(ids)

        def split_commit(ctx):
            tb.post_pending_transfers(ids)

        def payout(ctx):
            # concessionaire escrow → settlement payout (still two-phase)
            escrow = build_hold_chain(
                idempotency_key=key + ":payout",
                source_account=2099,
                legs=[("settlement", 9100, 10_000)],
            )
            tb.create_transfers(escrow)
            tb.post_pending_transfers([t.id for t in escrow])

        def publish(ctx):
            OutboxWriter(outbox).write(
                domain_record={"flow": key, "amount": AMOUNT},
                topic="fundsflow.completed",
                event_payload={"flow": key, "amount": AMOUNT},
                idempotency_key=key,
            )
            relay.publish_pending()

        return [
            SagaStep("hold", hold, undo_hold, SagaState.HELD),
            SagaStep("split_commit", split_commit, None, SagaState.SPLIT_COMMITTED),
            SagaStep("payout", payout, None, SagaState.PAYOUT_DONE),
            SagaStep("event_publish", publish, None, SagaState.EVENT_PUBLISHED),
        ]

    def run(self, key):
        payload = {"amount": AMOUNT, "source": SOURCE}
        return self.idem.execute(key, payload, lambda: self.coord.execute(
            self.coord.begin(key), self.steps_for(key)))


def total_value(tb):
    return sum(tb.balances.values())


def test_happy_path_conserves_value_and_publishes():
    f = Flow()
    before = total_value(f.tb)
    rec = f.run("pay-1")
    assert rec.state == SagaState.COMPLETED
    assert total_value(f.tb) == before  # conservation
    assert f.tb.balance(3001) == 65_001  # CRF gets the rounding remainder
    assert f.tb.balance(9100) == 10_000  # settlement payout credited
    assert len(f.bus.published) == 1
    assert f.enforcer.audit.verify() == []


def test_crash_after_hold_recovery_no_double_credit():
    f = Flow()
    f.saga_store.crash_on_save_state = SagaState.HELD
    with pytest.raises(RuntimeError):
        f.run("pay-2")
    # process "restarts": clear the fault, run recovery
    f.saga_store.crash_on_save_state = None
    rec = f.saga_store.by_idempotency_key("pay-2")
    recovered = f.coord.recover_unfinished(f.steps_for("pay-2"))
    assert recovered[0].state == SagaState.COMPLETED
    # exactly one hold chain exists; balances moved exactly once
    assert f.tb.balance(SOURCE) == 10_000_000 - AMOUNT
    # split accounts net AMOUNT minus the 10_000 paid out of escrow (2099)
    assert sum(f.tb.balance(a) for _, a, _ in SPLIT_PCTS) == AMOUNT - 10_000


def test_crash_after_partial_commit_replay_is_idempotent():
    f = Flow()
    # fail the first POST attempt after hold succeeded
    f.tb.fail_on_post = True
    rec = f.run("pay-3")
    assert rec.state == SagaState.COMPENSATED  # saga voids the hold
    assert f.tb.balance(SOURCE) == 10_000_000  # nothing moved
    f.tb.fail_on_post = False
    rec2 = f.run("pay-3")  # duplicate webhook: same key, same payload
    # idempotency middleware returns the original (compensated) result —
    # no re-execution, so still no movement and no double credit
    assert rec2.state == SagaState.COMPENSATED
    assert f.tb.balance(SOURCE) == 10_000_000


def test_crash_before_outbox_publish_recovery_scan_no_event_loss():
    f = Flow()
    f.bus.fail_next = True  # relay publish dies after domain+event committed
    rec = f.run("pay-4")
    # event row is committed but un-acked — no loss, relay scan heals it
    assert len(f.outbox_store.unacked()) == 1
    assert f.relay.publish_pending() == 1
    assert len(f.bus.published) == 1
    assert f.bus.published[0][1]["idempotency_key"] == "pay-4"


def test_duplicate_webhook_delivery_single_execution():
    f = Flow()
    r1 = f.run("pay-5")
    r2 = f.run("pay-5")  # duplicate delivery
    assert r1.saga_id == r2.saga_id
    assert f.idem.executions == 1
    assert f.tb.balance(3001) == 65_001  # credited exactly once


def test_replay_attack_same_key_different_payload_409_no_movement():
    f = Flow()
    f.run("pay-6")
    before = dict(f.tb.balances)
    with pytest.raises(IdempotencyConflict) as exc:
        f.idem.execute("pay-6", {"amount": 1, "source": SOURCE}, lambda: None)
    assert exc.value.status_code == 409
    assert f.tb.balances == before  # attacker moved nothing


def test_no_partial_split_under_mid_chain_crash():
    f = Flow()
    f.tb.fail_on_leg = "SECURITY_TRUST_FUND"
    rec = f.run("pay-7")
    assert rec.state == SagaState.COMPENSATED
    for _, account, _ in SPLIT_PCTS:
        assert f.tb.balance(account) == 0  # all-or-nothing: no partial legs
    assert f.tb.balance(SOURCE) == 10_000_000
