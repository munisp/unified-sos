"""Event bus: env selection (fail-closed), topic registry, payload parity."""
import asyncio
import json
import threading

import pytest
from pydantic import BaseModel

from eventbus import (
    AdapterUnavailableError,
    EventBusConfigError,
    FluvioEdgeBus,
    InMemoryEventBus,
    KafkaEventBus,
    event_bus_from_env,
    load_topic_map,
    topic_for_channel,
)


class SamplePayload(BaseModel):
    state_id: str
    amount_kobo: int


PAYLOAD = SamplePayload(state_id="nasarawa", amount_kobo=42_500_00)
CHANNEL = "ng.sos.mining.consignment_dispatched"


class TestTopicRegistry:
    def test_registry_loads_and_maps_channel_to_topic(self):
        topics = load_topic_map()
        assert topics[CHANNEL] == CHANNEL

    def test_unknown_channel_fails_closed(self):
        with pytest.raises(EventBusConfigError):
            topic_for_channel("ng.sos.does.not_exist")

    def test_missing_registry_file_fails_closed(self, tmp_path):
        with pytest.raises(EventBusConfigError):
            load_topic_map(tmp_path / "nope.json")


class TestEnvSelection:
    def test_default_is_in_memory(self):
        assert isinstance(event_bus_from_env({}), InMemoryEventBus)

    def test_kafka_requires_bootstrap(self):
        with pytest.raises(EventBusConfigError):
            event_bus_from_env({"EVENT_BUS": "kafka"})

    def test_kafka_uses_bootstrap_env(self):
        bus = event_bus_from_env(
            {"EVENT_BUS": "kafka", "EVENT_KAFKA_BOOTSTRAP": "broker:9092"}
        )
        assert isinstance(bus, KafkaEventBus)
        assert bus.bootstrap_servers == "broker:9092"

    def test_unknown_bus_fails_closed(self):
        with pytest.raises(EventBusConfigError):
            event_bus_from_env({"EVENT_BUS": "nats"})

    def test_fluvio_without_package_fails_closed(self):
        try:
            import fluvio  # noqa: F401

            pytest.skip("fluvio installed; seam test requires its absence")
        except ImportError:
            pass
        with pytest.raises(AdapterUnavailableError):
            FluvioEdgeBus()


class TestKafkaEventBus:
    def test_constructor_requires_bootstrap(self, monkeypatch):
        monkeypatch.delenv("EVENT_KAFKA_BOOTSTRAP", raising=False)
        with pytest.raises(EventBusConfigError):
            KafkaEventBus()

    def test_unknown_channel_rejected(self):
        bus = KafkaEventBus(bootstrap_servers="broker:9092")
        with pytest.raises(EventBusConfigError):
            bus.resolve_topic("ng.sos.does.not_exist")

    def test_subscribe_without_aiokafka_fails_closed(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_aiokafka(name, *args, **kwargs):
            if name == "aiokafka":
                raise ImportError("no aiokafka in sandbox")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_aiokafka)
        bus = KafkaEventBus(bootstrap_servers="broker:9092")
        with pytest.raises(AdapterUnavailableError):
            bus.subscribe(CHANNEL, lambda p: None)


class _FakeMessage:
    def __init__(self, value: bytes):
        self.value = value


class _FakeConsumer:
    """Fake aiokafka.AIOKafkaConsumer: replays queued messages, then idles."""

    instances = []

    def __init__(self, topic, bootstrap_servers=None, group_id=None, messages=()):
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self._messages = list(messages)
        self.started = False
        self.stopped = False
        _FakeConsumer.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for msg in self._messages:
            yield msg
        # idle until stopped, like a real consumer awaiting records
        while not self.stopped:
            await asyncio.sleep(0.01)


def _kafka_bus_with_fake(messages):
    _FakeConsumer.instances = []

    def factory(topic, bootstrap_servers=None, group_id=None):
        return _FakeConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            messages=messages,
        )

    return KafkaEventBus(bootstrap_servers="broker:9092", consumer_factory=factory)


class TestKafkaSubscribe:
    def test_messages_route_to_handler(self):
        bus = _kafka_bus_with_fake(
            [_FakeMessage(PAYLOAD.model_dump_json().encode("utf-8"))]
        )
        received = []
        done = threading.Event()

        def handler(payload):
            received.append(payload)
            done.set()

        bus.subscribe(CHANNEL, handler)
        assert done.wait(timeout=5)
        assert received == [json.loads(PAYLOAD.model_dump_json())]
        consumer = _FakeConsumer.instances[0]
        assert consumer.topic == CHANNEL
        assert consumer.bootstrap_servers == "broker:9092"
        bus.close()
        assert consumer.started

    def test_bad_json_is_dead_lettered_not_fatal(self, caplog):
        bus = _kafka_bus_with_fake(
            [
                _FakeMessage(b"not-json{"),
                _FakeMessage(PAYLOAD.model_dump_json().encode("utf-8")),
            ]
        )
        received = []
        done = threading.Event()

        def handler(payload):
            received.append(payload)
            done.set()

        with caplog.at_level("ERROR", logger="eventbus"):
            bus.subscribe(CHANNEL, handler)
            assert done.wait(timeout=5)
        # poison message dead-lettered, good message still delivered
        assert len(bus.dead_letters) == 1
        assert bus.dead_letters[0]["topic"] == CHANNEL
        assert received == [json.loads(PAYLOAD.model_dump_json())]
        bus.close()

    def test_handler_exception_does_not_kill_consumer(self, caplog):
        bus = _kafka_bus_with_fake(
            [
                _FakeMessage(PAYLOAD.model_dump_json().encode("utf-8")),
                _FakeMessage(PAYLOAD.model_dump_json().encode("utf-8")),
            ]
        )
        calls = []
        second = threading.Event()

        def handler(payload):
            calls.append(payload)
            if len(calls) == 1:
                raise RuntimeError("boom")
            second.set()

        with caplog.at_level("ERROR", logger="eventbus"):
            bus.subscribe(CHANNEL, handler)
            assert second.wait(timeout=5)
        assert len(calls) == 2
        bus.close()

    def test_close_stops_consumer_and_joins_thread(self):
        bus = _kafka_bus_with_fake([])
        bus.subscribe(CHANNEL, lambda p: None)
        consumer = _FakeConsumer.instances[0]
        bus.close()
        for t in bus._threads:
            assert not t.is_alive()


class _FakeProducer:
    """Captures send_and_wait calls like aiokafka.AIOKafkaProducer."""

    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value):
        self.sent.append((topic, value))


class TestPayloadParity:
    def test_inmemory_and_kafka_serialize_identically(self):
        """The AsyncAPI JSON payload is byte-identical across transports."""
        mem = InMemoryEventBus()
        mem.publish(CHANNEL, PAYLOAD)

        kafka = KafkaEventBus(bootstrap_servers="broker:9092")
        kafka._producer = _FakeProducer()  # bypass aiokafka; no broker needed
        asyncio.run(kafka.publish_async(CHANNEL, PAYLOAD))

        topic, value = kafka._producer.sent[0]
        assert topic == mem.published[0]["topic"] == CHANNEL
        assert json.loads(value.decode("utf-8")) == mem.published[0]["payload"]
        assert value.decode("utf-8") == PAYLOAD.model_dump_json()


@pytest.mark.skipif(
    not __import__("os").environ.get("EVENT_KAFKA_BOOTSTRAP"),
    reason="no Kafka broker in sandbox/CI; set EVENT_KAFKA_BOOTSTRAP to run",
)
class TestKafkaIntegration:
    def test_publish_round_trip(self):
        pytest.importorskip("aiokafka")
        bus = KafkaEventBus()  # bootstrap from EVENT_KAFKA_BOOTSTRAP
        bus.publish(CHANNEL, PAYLOAD)
