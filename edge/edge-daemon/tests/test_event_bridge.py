"""Sync engine event-backbone bridge: acked records are published as
AsyncAPI payloads through the configured bus (default: no bus)."""
import random

import pytest

from edge_daemon.crypto import DeviceSigner
from edge_daemon.daemon import EdgeDaemon
from edge_daemon.gateway import FakeGatewayState, SyncASGITransport, create_gateway_app
from edge_daemon.sync import (
    DEFAULT_EVENT_CHANNELS,
    SyncEngine,
    sync_engine_from_env,
)
from tests.test_sync import DEVICE, ticket

import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[3] / "services" / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from eventbus import InMemoryEventBus  # noqa: E402


@pytest.fixture
def gateway():
    state = FakeGatewayState()
    app = create_gateway_app(state)
    return state, SyncASGITransport(app)


def make_daemon(tmp_path):
    return EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))


def test_acked_records_published_to_bus(tmp_path, gateway):
    _, transport = gateway
    daemon = make_daemon(tmp_path)
    for i in range(3):
        daemon.issue(ticket(i + 1))
    bus = InMemoryEventBus()
    engine = SyncEngine(
        daemon.outbox, transport=transport, sleep=lambda s: None,
        rng=random.Random(42), bus=bus,
    )
    assert engine.sync_all() == 3
    assert len(bus.published) == 3
    for record in bus.published:
        assert record["topic"] == DEFAULT_EVENT_CHANNELS["revenue_ticket"]
        assert record["payload"]["kind"] == "revenue_ticket"
        assert record["payload"]["state_id"] == "nasarawa"
    engine.close()
    daemon.close()


def test_no_bus_by_default(tmp_path, gateway):
    _, transport = gateway
    daemon = make_daemon(tmp_path)
    daemon.issue(ticket(1))
    engine = SyncEngine(
        daemon.outbox, transport=transport, sleep=lambda s: None,
        rng=random.Random(42),
    )
    assert engine.bus is None
    assert engine.sync_all() == 1
    engine.close()
    daemon.close()


def test_publish_failure_leaves_records_pending(tmp_path, gateway):
    _, transport = gateway
    daemon = make_daemon(tmp_path)
    daemon.issue(ticket(1))

    class FailingBus:
        def publish(self, topic, payload):
            raise RuntimeError("broker down")

        def subscribe(self, topic, handler): ...

    engine = SyncEngine(
        daemon.outbox, transport=transport, sleep=lambda s: None,
        rng=random.Random(42), bus=FailingBus(),
    )
    with pytest.raises(RuntimeError, match="broker down"):
        engine.sync_once()
    assert daemon.outbox.pending_count() == 1  # not marked synced; retried later
    engine.close()
    daemon.close()


def test_env_factory_without_event_bus_is_gateway_only(tmp_path, gateway, monkeypatch):
    monkeypatch.delenv("EVENT_BUS", raising=False)
    _, transport = gateway
    daemon = make_daemon(tmp_path)
    engine = sync_engine_from_env(
        daemon.outbox, transport=transport, sleep=lambda s: None,
    )
    assert engine.bus is None
    engine.close()
    daemon.close()


def test_env_factory_kafka_requires_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "kafka")
    monkeypatch.delenv("EVENT_KAFKA_BOOTSTRAP", raising=False)
    daemon = make_daemon(tmp_path)
    with pytest.raises(Exception, match="EVENT_KAFKA_BOOTSTRAP"):
        sync_engine_from_env(daemon.outbox)
    daemon.close()
