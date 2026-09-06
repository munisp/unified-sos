"""Pluggable event bus.

Production deployments publish ``ng.sos.mining.*`` onto Kafka (or Fluvio on
the edge streaming tier). The :class:`EventBus` protocol keeps the service
transport-agnostic:

- :class:`InMemoryEventBus` — default for local dev and tests; records every
  published event for assertions and supports subscribe-style consumers.
- :class:`KafkaEventBus` — thin adapter; requires the optional ``aiokafka``
  package (Apache-2.0). Construct with ``KafkaEventBus(bootstrap_servers=...)``.
  It is intentionally small: serialization is the AsyncAPI JSON payload.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable, Dict, List, Protocol

from pydantic import BaseModel


class EventBus(Protocol):
    def publish(self, topic: str, payload: BaseModel) -> None: ...

    def subscribe(self, topic: str, handler: Callable[[BaseModel], None]) -> None: ...


class InMemoryEventBus:
    """Synchronous in-memory bus (tests/local)."""

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
    """Kafka/Fluvio adapter (production).

    Fluvio exposes a Kafka-compatible API, so the same adapter serves both
    the central cluster and the edge streaming pipeline. The producer is
    created lazily so importing this module never requires ``aiokafka``.
    """

    def __init__(self, bootstrap_servers: str, client_id: str = "mod-mining") -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self._producer = None  # aiokafka.AIOProducer, created lazily

    async def _ensure_producer(self) -> None:
        if self._producer is None:
            from aiokafka import AIOKafkaProducer  # optional dependency

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers, client_id=self.client_id
            )
            await self._producer.start()

    async def publish_async(self, topic: str, payload: BaseModel) -> None:
        await self._ensure_producer()
        await self._producer.send_and_wait(
            topic, payload.model_dump_json().encode("utf-8")
        )

    def publish(self, topic: str, payload: BaseModel) -> None:  # pragma: no cover
        import asyncio

        asyncio.run(self.publish_async(topic, payload))

    def subscribe(self, topic: str, handler: Callable[[BaseModel], None]) -> None:
        raise NotImplementedError(
            "Kafka consumption runs in dedicated consumers; use aiokafka "
            "AIOKafkaConsumer with the AsyncAPI schema for the topic."
        )
