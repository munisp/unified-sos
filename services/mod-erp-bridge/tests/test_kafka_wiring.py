"""mod-erp-bridge boots with EVENT_BUS=kafka (fake broker; aiokafka mocked).

The shared KafkaEventBus consumer is exercised without a real Kafka cluster:
a fake ``aiokafka`` module is injected into ``sys.modules`` and a canned
settlement record is replayed through the consumer into ``ingest_settlement``.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import types

import pytest

from app.service import SETTLEMENT_CHANNEL, build_service


class _FakeMessage:
    def __init__(self, value: bytes):
        self.value = value


class _FakeConsumer:
    """Minimal aiokafka.AIOKafkaConsumer stand-in (replay-then-idle)."""

    def __init__(self, topic, bootstrap_servers=None, group_id=None, messages=()):
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self._messages = list(messages)
        self.stopped = False

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for msg in self._messages:
            yield msg
        while not self.stopped:
            await asyncio.sleep(0.01)


SETTLEMENT_PAYLOAD = {
    "state_id": "nasarawa",
    "bill_reference": "BR-KAFKA-1",
    "timestamp": "2025-01-15T10:30:00+00:00",
    "splits": [
        {"beneficiary": "state_treasury", "amount_kobo": 150_000_00},
    ],
}


def _fake_aiokafka_module(messages):
    def factory(topic, bootstrap_servers=None, group_id=None):
        return _FakeConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            messages=messages,
        )

    module = types.ModuleType("aiokafka")
    module.AIOKafkaConsumer = factory
    return module


@pytest.fixture()
def kafka_env(monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "kafka")
    monkeypatch.setenv("EVENT_KAFKA_BOOTSTRAP", "fake-bootstrap:9092")
    monkeypatch.setenv("ERP_BACKEND", "local")
    monkeypatch.delenv("SOS_ERP_DSN", raising=False)


def test_boots_with_kafka_bus_and_consumes_settlement(kafka_env, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "aiokafka",
        _fake_aiokafka_module(
            [_FakeMessage(json.dumps(SETTLEMENT_PAYLOAD).encode("utf-8"))]
        ),
    )
    service = build_service()  # reads EVENT_BUS / EVENT_KAFKA_BOOTSTRAP from env
    try:
        bus = service._bus
        assert type(bus).__name__ == "KafkaEventBus"
        # settlement message routes through the consumer into ingestion
        deadline = threading.Event()

        def _poll():
            while not deadline.is_set():
                if service.list_journals("nasarawa"):
                    return
        poll_thread = threading.Thread(target=_poll)
        poll_thread.start()
        poll_thread.join(timeout=5)
        deadline.set()
        entries = service.list_journals("nasarawa")
        assert entries, "settlement event never reached ingest_settlement"
        assert entries[0].entry_id == "SETTLE-BR-KAFKA-1"
        # subscribed to the AsyncAPI settlement topic
        assert bus.topic_map[SETTLEMENT_CHANNEL] in [
            c.topic for c in bus._consumers
        ]
    finally:
        service._bus.close()


def test_kafka_bus_close_is_clean(kafka_env, monkeypatch):
    monkeypatch.setitem(sys.modules, "aiokafka", _fake_aiokafka_module([]))
    service = build_service()
    bus = service._bus
    bus.close()
    assert all(not t.is_alive() for t in bus._threads)
    assert all(c.stopped for c in bus._consumers)


def test_kafka_bus_without_aiokafka_fails_closed(kafka_env, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_aiokafka(name, *args, **kwargs):
        if name == "aiokafka":
            raise ImportError("no aiokafka in sandbox")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_aiokafka)
    monkeypatch.delitem(sys.modules, "aiokafka", raising=False)
    from _shared.eventbus import AdapterUnavailableError

    with pytest.raises(AdapterUnavailableError):
        build_service()
