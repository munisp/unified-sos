"""Sync engine: batch push, dedupe, out-of-order/duplicate rejection, latency."""
import random
import time

import httpx
import pytest

from edge_daemon.crypto import DeviceSigner
from edge_daemon.daemon import EdgeDaemon
from edge_daemon.gateway import FakeGatewayState, SyncASGITransport, create_gateway_app
from edge_daemon.models import RevenueTicketPayload, SignedRecord, SyncBatch
from edge_daemon.sync import SyncEngine, SyncFailedError

DEVICE = "POS-NAS-KOKO-003"


def ticket(n: int) -> RevenueTicketPayload:
    return RevenueTicketPayload(
        state_id="nasarawa",
        bill_reference=f"BR-NAS-{n:06d}",
        payer_id=f"MINER-{n:04d}",
        levy_code="110",
        amount_kobo=425_000_00,
        collector_id="AGENT-3",
    )


@pytest.fixture
def gateway():
    state = FakeGatewayState()
    app = create_gateway_app(state)
    return state, SyncASGITransport(app)


def make_engine(daemon, transport, **kw):
    kw.setdefault("sleep", lambda s: None)  # no real sleeping in tests
    kw.setdefault("rng", random.Random(42))
    return SyncEngine(daemon.outbox, transport=transport, **kw)


def test_sync_drains_outbox(tmp_path, gateway):
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    for i in range(1_200):
        daemon.issue(ticket(i))
    engine = make_engine(daemon, transport, batch_size=500)
    assert engine.sync_all() == 1_200
    assert daemon.outbox.pending_count() == 0
    assert len(state.accepted) == 1_200
    assert state.high_water[DEVICE] == 1_200
    engine.close()
    daemon.close()


def test_duplicate_rejection_is_idempotent(tmp_path, gateway):
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    daemon.issue(ticket(1))
    engine = make_engine(daemon, transport)

    rec = daemon.outbox.get(1)
    batch = SyncBatch(device_id=DEVICE, records=[rec])
    r1 = state.handle(batch)
    r2 = state.handle(batch)  # client retry after ack lost
    assert r1.acks[0].status == "accepted"
    assert r2.acks[0].status == "duplicate"
    assert len(state.accepted) == 1  # no double-posting
    engine.close()
    daemon.close()


def test_out_of_order_rejection_server_side(tmp_path, gateway):
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    for i in range(3):
        daemon.issue(ticket(i))

    r3, r1 = daemon.outbox.get(3), daemon.outbox.get(1)
    # Gap skip: sequence 3 before 1
    res = state.handle(SyncBatch(device_id=DEVICE, records=[r3]))
    assert res.acks[0].status == "rejected"
    assert "out-of-order" in res.acks[0].detail

    # Now send 1, then replay an old record below the high-water mark
    state.handle(SyncBatch(device_id=DEVICE, records=[r1]))
    stale = r1.model_copy(update={"sequence": 1})
    res = state.handle(SyncBatch(device_id=DEVICE, records=[stale]))
    assert res.acks[0].status == "duplicate"  # seen -> idempotent
    unseen_old = r3.model_copy(update={"sequence": 2, "signature": "bogus"})
    res = state.handle(SyncBatch(device_id=DEVICE, records=[unseen_old]))
    assert res.acks[0].status == "rejected"
    daemon.close()


def test_bad_signature_rejected_server_side(tmp_path, gateway):
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    daemon.issue(ticket(1))
    rec = daemon.outbox.get(1)
    forged = rec.model_copy(update={"signature": rec.signature[:-4] + "AAAA"})
    res = state.handle(SyncBatch(device_id=DEVICE, records=[forged]))
    assert res.acks[0].status == "rejected"
    assert "signature" in res.acks[0].detail
    assert len(state.accepted) == 0
    daemon.close()


def test_retry_with_backoff_then_success(tmp_path, gateway, monkeypatch):
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    daemon.issue(ticket(1))
    engine = make_engine(daemon, transport)

    calls = {"n": 0}
    real_post = engine._post_batch

    def flaky(batch):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("cellular down", request=httpx.Request("POST", "http://x"))
        return real_post(batch)

    monkeypatch.setattr(engine, "_post_batch", flaky)
    assert engine.sync_once() == 1
    assert calls["n"] == 3  # two failures + one success
    engine.close()
    daemon.close()


def test_gives_up_after_max_retries(tmp_path, gateway, monkeypatch):
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    daemon.issue(ticket(1))
    engine = make_engine(daemon, transport, max_retries=2)

    def always_down(batch):
        raise httpx.ConnectError("no signal", request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(engine, "_post_batch", always_down)
    with pytest.raises(SyncFailedError):
        engine.sync_once()
    assert daemon.outbox.pending_count() == 1  # still buffered for next reconnect
    engine.close()
    daemon.close()


def test_sync_latency_sanity(tmp_path, gateway):
    """SLO: sync on connect < 3s — here 1,000 records in-process, no backoff sleeps."""
    state, transport = gateway
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    for i in range(1_000):
        daemon.issue(ticket(i))
    engine = make_engine(daemon, transport, batch_size=1_000)
    start = time.perf_counter()
    assert engine.sync_all() == 1_000
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"sync of 1000 records took {elapsed:.2f}s (SLO < 3s)"
    engine.close()
    daemon.close()


def test_resumable_sync_after_restart(tmp_path, gateway):
    """Crash mid-sync: reopen DB, new engine continues exactly where it stopped."""
    state, transport = gateway
    db = tmp_path / "edge.db"
    signer = DeviceSigner.generate(DEVICE)
    daemon = EdgeDaemon(db, DEVICE, signer=signer)
    for i in range(10):
        daemon.issue(ticket(i))
    engine = make_engine(daemon, transport, batch_size=4)
    assert engine.sync_once() == 4  # partial sync, then "crash"
    engine.close()
    daemon.close()

    resumed = EdgeDaemon.reopen(db, DEVICE, signer=signer)
    engine2 = make_engine(resumed, transport, batch_size=4)
    assert engine2.sync_all() == 6
    assert resumed.outbox.pending_count() == 0
    assert len(state.accepted) == 10  # no duplicates from the first 4
    engine2.close()
    resumed.close()
