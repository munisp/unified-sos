"""Event bus: env selection (fail-closed), topic registry, payload parity."""
import asyncio
import json

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

    def test_subscribe_not_supported(self):
        bus = KafkaEventBus(bootstrap_servers="broker:9092")
        with pytest.raises(NotImplementedError):
            bus.subscribe(CHANNEL, lambda p: None)


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
