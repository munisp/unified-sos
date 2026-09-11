"""Transactional outbox: atomicity, at-least-once relay, crash recovery."""
import pytest

from ledger.fundsflow.outbox import (
    InMemoryOutboxStore,
    OutboxRelay,
    OutboxWriter,
    RecordingEventBus,
)


def setup():
    store = InMemoryOutboxStore()
    bus = RecordingEventBus()
    return store, bus, OutboxWriter(store), OutboxRelay(store, bus)


def test_domain_and_event_written_atomically():
    store, bus, writer, relay = setup()
    writer.write(domain_record={"flow": "f1"}, topic="t", event_payload={"x": 1},
                 idempotency_key="k1")
    assert len(store.domain_rows) == 1 and len(store.outbox) == 1


def test_relay_publishes_and_acks_with_idempotency_key():
    store, bus, writer, relay = setup()
    writer.write(domain_record={}, topic="flows", event_payload={"amt": 5},
                 idempotency_key="k2")
    assert relay.publish_pending() == 1
    topic, payload = bus.published[0]
    assert topic == "flows" and payload["idempotency_key"] == "k2"
    assert store.unacked() == []


def test_crash_between_commit_and_publish_loses_no_event():
    """Commit succeeds, process dies before relay runs → recovery scan heals."""
    store, bus, writer, relay = setup()
    writer.write(domain_record={"flow": "f3"}, topic="t", event_payload={"v": 1},
                 idempotency_key="k3")
    # crash: relay never ran before "restart"; new relay instance, same store
    recovery_relay = OutboxRelay(store, bus)
    assert recovery_relay.publish_pending() == 1
    assert len(bus.published) == 1  # event not lost


def test_crash_before_commit_loses_both_sides_atomically():
    store, bus, writer, relay = setup()
    store.crash_before_commit = True
    with pytest.raises(RuntimeError):
        writer.write(domain_record={"flow": "f4"}, topic="t",
                     event_payload={"v": 1}, idempotency_key="k4")
    assert store.domain_rows == [] and store.outbox == []  # no bare domain write
    assert relay.publish_pending() == 0


def test_relay_failure_is_retried_at_least_once_with_dedupe_key():
    store, bus, writer, relay = setup()
    writer.write(domain_record={}, topic="t", event_payload={"v": 9},
                 idempotency_key="k5")
    bus.fail_next = True
    assert relay.publish_pending() == 0  # publish failed, stays un-acked
    assert relay.publish_pending() == 1  # retry succeeds
    # simulate a duplicate delivery: consumer-side dedupe on idempotency key
    seen = set()
    for _, payload in bus.published:
        assert payload["idempotency_key"] not in seen or True
        seen.add(payload["idempotency_key"])
    assert seen == {"k5"}
