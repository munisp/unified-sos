"""Outbox: seq ordering, dead-letter, Postgres seam, reconciler grace."""
import pytest

from ledger.fundsflow.outbox import (
    DEFAULT_MAX_ATTEMPTS,
    OUTBOX_DEADLETTER_TOPIC,
    AdapterUnavailableError,
    InMemoryOutboxStore,
    OutboxRelay,
    OutboxWriter,
    PostgresOutboxStore,
    RecordingEventBus,
    select_outbox_store,
)
from ledger.fundsflow.reconcile import Reconciler
from ledger.fundsflow.tigerbeetle_flows import InMemoryTBClient


def setup():
    store = InMemoryOutboxStore()
    bus = RecordingEventBus()
    return store, bus, OutboxWriter(store), OutboxRelay(store, bus)


def test_rows_get_monotonic_seq_and_publish_in_seq_order():
    store, bus, writer, relay = setup()
    writer.write(domain_record={}, topic="t", event_payload={"n": 1}, idempotency_key="a")
    writer.write(domain_record={}, topic="t", event_payload={"n": 2}, idempotency_key="b")
    writer.write(domain_record={}, topic="t", event_payload={"n": 3}, idempotency_key="c")
    seqs = [r.seq for r in store.outbox]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3 and seqs[0] > 0
    relay.publish_pending()
    assert [p["n"] for _, p in bus.published] == [1, 2, 3]


def test_dead_letter_after_max_attempts_and_skipped_thereafter():
    store, bus, writer, _ = setup()

    class AlwaysFailBus(RecordingEventBus):
        def publish(self, topic, payload):
            if topic == OUTBOX_DEADLETTER_TOPIC:
                return super().publish(topic, payload)
            raise RuntimeError("bus down")

    bus = AlwaysFailBus()
    relay = OutboxRelay(store, bus, max_attempts=3)
    writer.write(domain_record={}, topic="t", event_payload={"x": 1}, idempotency_key="d")
    for _ in range(3):
        assert relay.publish_pending() == 0
    row = store.outbox[0]
    assert row.dead and row.attempts == 3
    # dead-letter event emitted
    assert bus.published[-1][0] == OUTBOX_DEADLETTER_TOPIC
    assert bus.published[-1][1]["event_id"] == row.event_id
    # skipped in future scans even when the bus recovers
    bus2 = RecordingEventBus()
    assert OutboxRelay(store, bus2).publish_pending() == 0
    assert bus2.published == []


def test_default_max_attempts_is_eight():
    assert DEFAULT_MAX_ATTEMPTS == 8
    store, bus, writer, relay = setup()
    assert relay.max_attempts == 8


def test_postgres_seam_fail_closed_without_dsn(monkeypatch):
    monkeypatch.delenv("SOS_FUNDSFLOW_OUTBOX_DSN", raising=False)
    with pytest.raises(AdapterUnavailableError):
        PostgresOutboxStore()


def test_select_store_fail_closed_in_production(monkeypatch):
    monkeypatch.delenv("SOS_FUNDSFLOW_OUTBOX_DSN", raising=False)
    monkeypatch.setenv("SOS_FUNDSFLOW_PROFILE", "production")
    with pytest.raises(AdapterUnavailableError):
        select_outbox_store()
    monkeypatch.setenv("SOS_FUNDSFLOW_PROFILE", "dev")
    assert isinstance(select_outbox_store(), InMemoryOutboxStore)


def test_postgres_ddl_shape():
    ddl = PostgresOutboxStore.DDL.lower()
    assert "ledger_outbox" in ddl and "bigserial" in ddl
    assert "jsonb" in ddl and "dead" in ddl and "attempts" in ddl


def _reconciler(store, bus, **kw):
    return Reconciler(InMemoryTBClient(), store, bus, **kw)


def test_reconciler_grace_window_suppresses_fresh_unacked():
    now = [10_000.0]
    store = InMemoryOutboxStore(clock=lambda: now[0])
    bus = RecordingEventBus()
    OutboxWriter(store).write(domain_record={}, topic="t", event_payload={},
                              idempotency_key="g")
    rec = _reconciler(store, bus, clock=lambda: now[0])
    # fresh row (age 0 < 300s grace) → no alert
    assert rec.reconcile(expected_by_account={}).clean
    assert bus.published == []
    # age past grace → alert
    now[0] += 301
    report = rec.reconcile(expected_by_account={})
    assert any(m.kind == "LEDGER_VS_OUTBOX" for m in report.mismatches)


def test_reconciler_grace_configurable():
    now = [100.0]
    store = InMemoryOutboxStore(clock=lambda: now[0])
    bus = RecordingEventBus()
    OutboxWriter(store).write(domain_record={}, topic="t", event_payload={},
                              idempotency_key="g2")
    rec = _reconciler(store, bus, clock=lambda: now[0], outbox_grace_seconds=0)
    assert not rec.reconcile(expected_by_account={}).clean
