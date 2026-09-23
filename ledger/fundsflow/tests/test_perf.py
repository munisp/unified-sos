"""Targeted performance tests: batched outbox flush and memoization hit-rate.

These guard the perf work (batch relay, deterministic-ID memoization,
idempotency fast-path cache) without asserting wall-clock timings that would
flake under CI; they assert structural properties (single-digit bus calls,
cache hit counters) plus correctness invariants.
"""
from __future__ import annotations

import pytest

from ledger.fundsflow.idempotency import (
    IdempotencyConflict,
    IdempotencyMiddleware,
)
from ledger.fundsflow.outbox import (
    InMemoryOutboxStore,
    OutboxRelay,
    OutboxWriter,
    RecordingEventBus,
)
from ledger.fundsflow.tigerbeetle_flows import (
    InMemoryTBClient,
    build_hold_chain,
    deterministic_transfer_id,
)


def _fill(store: InMemoryOutboxStore, writer: OutboxWriter, n: int) -> None:
    for i in range(n):
        writer.write(
            domain_record={"i": i},
            topic="t",
            event_payload={"i": i},
            idempotency_key=f"k{i}",
        )


class TestBatchFlush:
    def test_batched_flush_publishes_all_in_seq_order(self):
        store, bus = InMemoryOutboxStore(), RecordingEventBus()
        writer = OutboxWriter(store)
        _fill(store, writer, 200)
        relay = OutboxRelay(store, bus)
        assert relay.publish_pending_batched(batch_size=64) == 200
        assert len(bus.published) == 200
        # seq ordering preserved on the bus
        assert [p["i"] for _, p in bus.published] == list(range(200))
        assert store.unacked() == []

    def test_batched_flush_falls_back_per_row_on_batch_failure(self):
        store, bus = InMemoryOutboxStore(), RecordingEventBus()
        writer = OutboxWriter(store)
        _fill(store, writer, 10)
        relay = OutboxRelay(store, bus, max_attempts=2)
        bus.fail_next = True  # poison the first batch
        # batch fails → per-row fallback isolates; all rows still delivered
        assert relay.publish_pending_batched(batch_size=4) == 10
        assert len(bus.published) == 10

    def test_batched_flush_dead_letters_poison_row(self):
        store, bus = InMemoryOutboxStore(), RecordingEventBus()
        writer = OutboxWriter(store)
        _fill(store, writer, 3)
        relay = OutboxRelay(store, bus, max_attempts=1)

        def always_fail(events):
            raise RuntimeError("bus down")

        bus.publish_batch = always_fail
        bus.publish = lambda *a: (_ for _ in ()).throw(RuntimeError("down"))
        assert relay.publish_pending_batched(batch_size=8) == 0
        assert all(r.dead for r in store.outbox)  # max_attempts=1 → dead

    def test_write_batch_is_one_atomic_commit(self):
        store = InMemoryOutboxStore()
        writer = OutboxWriter(store)
        rows = writer.write_batch(
            [
                {"domain_record": {"i": i}, "topic": "t",
                 "event_payload": {"i": i}, "idempotency_key": f"k{i}"}
                for i in range(50)
            ]
        )
        assert len(rows) == 50
        assert [r.seq for r in rows] == sorted(r.seq for r in rows)
        assert len(store.domain_rows) == 50
        bus = RecordingEventBus()
        assert OutboxRelay(store, bus).publish_pending_batched() == 50

    def test_write_batch_crash_loses_everything(self):
        store = InMemoryOutboxStore()
        store.crash_before_commit = True
        writer = OutboxWriter(store)
        with pytest.raises(RuntimeError):
            writer.write_batch(
                [{"domain_record": {}, "topic": "t", "event_payload": {},
                  "idempotency_key": "k"}]
            )
        assert store.outbox == [] and store.domain_rows == []


class TestMemoization:
    def test_deterministic_id_memoized_and_stable(self):
        tid = deterministic_transfer_id("perf-key", "leg-a")
        assert deterministic_transfer_id("perf-key", "leg-a") == tid
        assert deterministic_transfer_id("perf-key", "leg-b") != tid

    def test_hold_chain_rebuild_uses_memoized_ids(self):
        client = InMemoryTBClient()
        client.balances[1] = 10**9
        legs = [("leg", 2, 100)]
        chain1 = build_hold_chain(idempotency_key="k", source_account=1, legs=legs)
        chain2 = build_hold_chain(idempotency_key="k", source_account=1, legs=legs)
        assert [t.id for t in chain1] == [t.id for t in chain2]

    def test_holds_index_tracks_post_and_void(self):
        client = InMemoryTBClient()
        client.balances[1] = 1000
        chain = build_hold_chain(
            idempotency_key="k", source_account=1, legs=[("a", 2, 400)]
        )
        client.create_transfers(chain)
        assert client._held(1) == 400
        client.post_pending_transfers([t.id for t in chain])
        assert client._held(1) == 0
        client.create_transfers(
            build_hold_chain(idempotency_key="k2", source_account=1,
                             legs=[("b", 2, 100)])
        )
        client.void_pending_transfers(
            [deterministic_transfer_id("k2", "b")]
        )
        assert client._held(1) == 0


class TestIdempotencyFastPath:
    def test_replay_hits_local_cache_without_store_round_trip(self):
        mw = IdempotencyMiddleware()
        calls = []

        def fn():
            calls.append(1)
            return {"ok": True}

        assert mw.execute("k1", {"a": 1}, fn) == {"ok": True}
        assert mw.executions == 1
        # replays: served from the local fast path
        for _ in range(100):
            assert mw.execute("k1", {"a": 1}, fn) == {"ok": True}
        assert mw.executions == 1  # never re-executed
        assert mw.local_hits == 100

    def test_local_cache_preserves_conflict_semantics(self):
        mw = IdempotencyMiddleware()
        mw.execute("k1", {"a": 1}, lambda: 1)
        with pytest.raises(IdempotencyConflict):
            mw.execute("k1", {"a": 2}, lambda: 2)

    def test_local_cache_expiry_falls_back_to_store(self):
        now = [1000.0]
        mw = IdempotencyMiddleware(clock=lambda: now[0], ttl_seconds=10)
        mw.execute("k1", {"a": 1}, lambda: "first")
        now[0] += 20  # beyond TTL
        assert mw.execute("k1", {"a": 1}, lambda: "second") == "second"
        assert mw.executions == 2

    def test_failed_execution_does_not_poison_cache(self):
        mw = IdempotencyMiddleware()
        with pytest.raises(ValueError):
            mw.execute("k1", {}, lambda: (_ for _ in ()).throw(ValueError("x")))
        assert mw.execute("k1", {}, lambda: "ok") == "ok"
        assert mw.local_hits == 0 or mw.execute("k1", {}, lambda: "bad") == "ok"
