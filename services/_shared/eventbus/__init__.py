"""Shared pluggable event bus (P1 Workstream 3: Kafka/Fluvio event backbone).

The :class:`EventBus` protocol keeps services transport-agnostic:

- :class:`InMemoryEventBus` — default for local dev and tests; records every
  published event for assertions and supports subscribe-style consumers.
- :class:`KafkaEventBus` — production adapter over Kafka (central tier).
  Requires the optional ``aiokafka`` package (Apache-2.0); importing this
  module never requires it (import-guarded, fail-closed).
- :class:`FluvioEdgeBus` — edge streaming tier seam. Requires the optional
  ``fluvio`` package; raises :class:`AdapterUnavailableError` without it.

Selection is by environment (fail-closed, mirroring the
``services/mod-kyc-kyb/app/adapters/base.py`` idiom)::

    EVENT_BUS=memory|kafka|fluvio        (default: memory)
    EVENT_KAFKA_BOOTSTRAP=broker1:9092   (required when EVENT_BUS=kafka)

Topic mapping is generated from the AsyncAPI contracts
(``contracts/asyncapi/*.yaml``) into ``contracts/asyncapi/registry/topics.json``
by ``contracts/asyncapi/registry/generate.py`` — one topic per channel.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

from pydantic import BaseModel

_REGISTRY_DIR = Path(__file__).resolve().parents[3] / "contracts" / "asyncapi" / "registry"
_TOPICS_JSON = _REGISTRY_DIR / "topics.json"


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production adapter dependency is missing."""


class EventBusConfigError(RuntimeError):
    """Raised when the event-bus environment configuration is invalid."""


class EventBus(Protocol):
    def publish(self, topic: str, payload: BaseModel) -> None: ...

    def subscribe(self, topic: str, handler: Callable[[BaseModel], None]) -> None: ...


def load_topic_map(registry_path: Optional[Path] = None) -> Dict[str, str]:
    """Load the AsyncAPI channel → Kafka topic map generated from the contracts.

    Fail-closed: raises :class:`EventBusConfigError` if the generated
    registry file is missing (run ``contracts/asyncapi/registry/generate.py``).
    """
    path = Path(registry_path) if registry_path else _TOPICS_JSON
    if not path.exists():
        raise EventBusConfigError(
            f"topic registry not found at {path}; run "
            "contracts/asyncapi/registry/generate.py to generate it"
        )
    data = json.loads(path.read_text())
    return {entry["channel"]: entry["topic"] for entry in data["channels"]}


def topic_for_channel(channel: str, registry_path: Optional[Path] = None) -> str:
    """Resolve an AsyncAPI channel name to its Kafka topic."""
    topics = load_topic_map(registry_path)
    if channel not in topics:
        raise EventBusConfigError(
            f"unknown AsyncAPI channel {channel!r}; not in generated topic registry"
        )
    return topics[channel]


class InMemoryEventBus:
    """Synchronous in-memory bus (tests/local). Default bus."""

    def __init__(self) -> None:
        self.published: List[Dict[str, Any]] = []
        self._handlers: Dict[str, List[Callable[[BaseModel], None]]] = defaultdict(list)

    def publish(self, topic: str, payload: BaseModel) -> None:
        self.published.append(
            {"topic": topic, "payload": json.loads(payload.model_dump_json())}
        )
        for handler in self._handlers.get(topic, []):
            handler(payload)

    def subscribe(self, topic: str, handler: Callable[[BaseModel], None]) -> None:
        self._handlers[topic].append(handler)


class KafkaEventBus:
    """Kafka adapter (production, central tier).

    Topics come from the generated AsyncAPI registry (one topic per channel).
    Serialization is the AsyncAPI JSON payload (``model_dump_json``).
    The producer is created lazily so importing this module never requires
    ``aiokafka``; constructing without a bootstrap server is fail-closed.
    """

    def __init__(
        self,
        bootstrap_servers: Optional[str] = None,
        client_id: str = "sos-service",
        registry_path: Optional[Path] = None,
        consumer_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        servers = bootstrap_servers or os.environ.get("EVENT_KAFKA_BOOTSTRAP")
        if not servers:
            raise EventBusConfigError(
                "EVENT_KAFKA_BOOTSTRAP is required for the Kafka event bus "
                "(fail-closed: refusing to fall back to an implicit broker)"
            )
        self.bootstrap_servers = servers
        self.client_id = client_id
        self.topic_map = load_topic_map(registry_path)
        self._producer = None  # aiokafka.AIOKafkaProducer, created lazily
        # consumer_factory: test seam for injecting a fake AIOKafkaConsumer
        # class (same call signature); production code leaves it None and the
        # real aiokafka client is imported lazily in subscribe().
        self._consumer_factory = consumer_factory
        # Messages whose JSON deserialization fails land here (dead-letter,
        # mirroring the erp-bridge dead-letter style) and are logged.
        self.dead_letters: List[Dict[str, Any]] = []
        self._consumers: List[Any] = []
        self._threads: List[threading.Thread] = []
        self._loops: List[Any] = []  # asyncio loops, one per consumer thread
        self._closing = threading.Event()
        self._started = threading.Event()

    def resolve_topic(self, channel: str) -> str:
        if channel not in self.topic_map:
            raise EventBusConfigError(
                f"unknown AsyncAPI channel {channel!r}; not in generated topic registry"
            )
        return self.topic_map[channel]

    async def _ensure_producer(self) -> None:
        if self._producer is None:
            try:
                from aiokafka import AIOKafkaProducer  # optional dependency
            except ImportError as exc:
                raise AdapterUnavailableError(
                    "aiokafka is required for KafkaEventBus (pip install aiokafka)"
                ) from exc
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers, client_id=self.client_id
            )
            await self._producer.start()

    async def publish_async(self, channel: str, payload: BaseModel) -> None:
        await self._ensure_producer()
        await self._producer.send_and_wait(
            self.resolve_topic(channel), payload.model_dump_json().encode("utf-8")
        )

    def publish(self, topic: str, payload: BaseModel) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread: drive the producer directly.
            asyncio.run(self.publish_async(topic, payload))
            return
        # Already inside an event loop (e.g. a consumer-thread handler that
        # re-publishes): schedule onto it instead of nesting asyncio.run.
        asyncio.run_coroutine_threadsafe(self.publish_async(topic, payload), loop)

    def _build_consumer(self, topic: str) -> Any:
        """Create an AIOKafkaConsumer for ``topic`` (lazy import, fail-closed)."""
        factory = self._consumer_factory
        if factory is None:
            try:
                from aiokafka import AIOKafkaConsumer  # optional dependency
            except ImportError as exc:
                raise AdapterUnavailableError(
                    "aiokafka is required for KafkaEventBus.subscribe "
                    "(pip install aiokafka)"
                ) from exc
            factory = AIOKafkaConsumer
        return factory(
            topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.client_id,
        )

    def subscribe(self, topic: str, handler: Callable[[BaseModel], None]) -> None:
        """Subscribe ``handler`` to ``topic`` on a background consumer thread.

        Each message value is the AsyncAPI JSON payload (mirroring
        ``publish``); it is deserialized and handed to ``handler`` as a plain
        ``dict`` (handlers such as erp-bridge's ``ingest_settlement`` accept
        either a mapping or a pydantic model). Deserialization failures are
        logged and dead-lettered; they never kill the consumer loop.
        """
        consumer = self._build_consumer(topic)
        self._consumers.append(consumer)
        thread = threading.Thread(
            target=self._consume_loop,
            args=(consumer, topic, handler),
            name=f"kafka-consumer-{topic}",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()

    def _consume_loop(
        self, consumer: Any, topic: str, handler: Callable[[BaseModel], None]
    ) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        self._loops.append((loop, consumer))
        try:
            loop.run_until_complete(self._consume_async(consumer, topic, handler))
        finally:
            loop.close()

    async def _consume_async(
        self, consumer: Any, topic: str, handler: Callable[[BaseModel], None]
    ) -> None:
        await consumer.start()
        self._started.set()
        try:
            async for msg in consumer:
                if self._closing.is_set():
                    break
                try:
                    payload = json.loads(msg.value.decode("utf-8"))
                except Exception as exc:  # noqa: BLE001 — poison message
                    logger.exception(
                        "dead-lettering undecodable message on topic %s", topic
                    )
                    self.dead_letters.append(
                        {"topic": topic, "error": str(exc),
                         "value": getattr(msg, "value", b"")}
                    )
                    continue
                try:
                    handler(payload)
                except Exception:  # noqa: BLE001 — handler faults must not kill the loop
                    logger.exception("handler failed for message on topic %s", topic)
        finally:
            await consumer.stop()

    def close(self) -> None:
        """Signal consumer loops to stop and join their threads (best-effort)."""
        import asyncio

        self._closing.set()
        self._started.wait(timeout=5.0)  # let consumer threads reach start()
        for loop, consumer in list(self._loops):
            if loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(consumer.stop(), loop)
                except RuntimeError:  # loop already closed
                    pass
        for thread in self._threads:
            thread.join(timeout=5.0)


class FluvioEdgeBus:
    """Fluvio adapter seam for the edge streaming tier.

    Edge-only (the edge daemon sync bridge uses this when
    ``EVENT_BUS=fluvio``). Import-guarded: raises
    :class:`AdapterUnavailableError` when the optional ``fluvio`` package
    is not installed.
    """

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        client_id: str = "sos-edge",
    ) -> None:
        try:
            import fluvio  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "fluvio is required for FluvioEdgeBus (edge streaming tier); "
                "install the fluvio Python client on the edge device"
            ) from exc
        self.client_id = client_id
        self.topic_map = load_topic_map(registry_path)
        self._producer = None

    def publish(self, topic: str, payload: BaseModel) -> None:  # pragma: no cover
        raise NotImplementedError(
            "FluvioEdgeBus publish is wired on the edge image; the seam is "
            "import-guarded here so central services never depend on fluvio."
        )

    def subscribe(self, topic: str, handler: Callable[[BaseModel], None]) -> None:
        raise NotImplementedError(
            "Fluvio consumption runs on the edge streaming tier."
        )


def event_bus_from_env(environ: Optional[Dict[str, str]] = None) -> EventBus:
    """Build an event bus from ``EVENT_BUS`` / ``EVENT_KAFKA_BOOTSTRAP``.

    Fail-closed: unknown ``EVENT_BUS`` values and a missing bootstrap config
    raise instead of silently falling back to a different transport.
    """
    env = dict(os.environ if environ is None else environ)
    kind = env.get("EVENT_BUS", "memory").strip().lower()
    if kind in ("memory", "inmemory", "in-memory"):
        return InMemoryEventBus()
    if kind == "kafka":
        return KafkaEventBus(bootstrap_servers=env.get("EVENT_KAFKA_BOOTSTRAP"))
    if kind == "fluvio":
        return FluvioEdgeBus()
    raise EventBusConfigError(
        f"unknown EVENT_BUS {kind!r}; valid: memory, kafka, fluvio"
    )


__all__ = [
    "AdapterUnavailableError",
    "EventBus",
    "EventBusConfigError",
    "FluvioEdgeBus",
    "InMemoryEventBus",
    "KafkaEventBus",
    "event_bus_from_env",
    "load_topic_map",
    "topic_for_channel",
]
