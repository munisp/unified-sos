"""Transactional outbox: domain write + event write in ONE store transaction.

Guarantee: an event is never lost between the domain commit and the bus
publish. The relay delivers at-least-once; consumers dedupe via the
idempotency key carried on every event. A recovery scan republishes all
un-acked rows, so a crash after commit / before publish is healed on the
next relay pass.

Bus seam: recording in-memory bus by default; Kafka via the shared
``services/_shared/eventbus`` KafkaEventBus behind an import-guard.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol


class EventBusProtocol(Protocol):
    def publish(self, topic: str, payload: Dict[str, Any]) -> None: ...


class RecordingEventBus:
    """Default bus (tests/dev). Records every published event."""

    def __init__(self) -> None:
        self.published: List[tuple] = []
        self.fail_next: bool = False  # crash-injection hook

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected bus publish failure")
        self.published.append((topic, payload))


class KafkaOutboxBus:
    """Kafka seam over services/_shared/eventbus (import-guarded).

    Payloads are wrapped in a generic pydantic model so the shared
    KafkaEventBus contract (BaseModel payloads) is honoured.
    """

    def __init__(self, bus: Any) -> None:  # pragma: no cover - transport seam
        self._bus = bus

    @classmethod
    def from_shared(cls) -> "KafkaOutboxBus":  # pragma: no cover
        try:
            from _shared.eventbus import KafkaEventBus  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "shared eventbus unavailable; cannot build Kafka outbox bus"
            ) from exc
        return cls(KafkaEventBus())

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:  # pragma: no cover
        from pydantic import BaseModel

        class _Envelope(BaseModel):
            data: Dict[str, Any]

        self._bus.publish(topic, _Envelope(data=payload))


@dataclass
class OutboxRow:
    event_id: str
    topic: str
    payload: Dict[str, Any]
    idempotency_key: str
    created_at: float
    published: bool = False
    attempts: int = 0


class OutboxTransaction(Protocol):
    def write_domain(self, record: Dict[str, Any]) -> None: ...

    def write_event(self, row: OutboxRow) -> None: ...

    def commit(self) -> None: ...


class InMemoryOutboxStore:
    """Atomic single-transaction store: domain rows + outbox rows commit
    together. A crash before commit loses BOTH (never a bare domain write
    without its event); a crash after commit leaves an un-acked row that the
    relay recovery scan will publish."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.domain_rows: List[Dict[str, Any]] = []
        self.outbox: List[OutboxRow] = []
        self._clock = clock
        self.crash_before_commit: bool = False  # crash-injection hook

    def transaction(self) -> "InMemoryOutboxTransaction":
        return InMemoryOutboxTransaction(self)

    def unacked(self) -> List[OutboxRow]:
        return [r for r in self.outbox if not r.published]


class InMemoryOutboxTransaction:
    def __init__(self, store: InMemoryOutboxStore) -> None:
        self._store = store
        self._domain: List[Dict[str, Any]] = []
        self._events: List[OutboxRow] = []
        self._done = False

    def write_domain(self, record: Dict[str, Any]) -> None:
        self._domain.append(record)

    def write_event(self, row: OutboxRow) -> None:
        self._events.append(row)

    def commit(self) -> None:
        if self._done:
            raise RuntimeError("transaction already committed")
        if self._store.crash_before_commit:
            # simulated crash: NOTHING is persisted — atomicity preserved
            self._done = True
            raise RuntimeError("injected crash before commit")
        self._store.domain_rows.extend(self._domain)
        self._store.outbox.extend(self._events)
        self._done = True


class OutboxWriter:
    """Writes domain + event atomically."""

    def __init__(self, store: InMemoryOutboxStore) -> None:
        self.store = store

    def write(
        self,
        *,
        domain_record: Dict[str, Any],
        topic: str,
        event_payload: Dict[str, Any],
        idempotency_key: str,
    ) -> OutboxRow:
        row = OutboxRow(
            event_id=str(uuid.uuid4()),
            topic=topic,
            payload=event_payload,
            idempotency_key=idempotency_key,
            created_at=self.store._clock(),
        )
        tx = self.store.transaction()
        tx.write_domain(domain_record)
        tx.write_event(row)
        tx.commit()
        return row


class OutboxRelay:
    """At-least-once relay with recovery scan.

    ``publish_pending`` IS the recovery scan: it publishes every un-acked
    row and acks only on success. Re-running it after a crash republishes
    rows that were committed but never published. Consumers must dedupe on
    ``idempotency_key`` (at-least-once ⇒ duplicates are possible).
    """

    def __init__(self, store: InMemoryOutboxStore, bus: EventBusProtocol) -> None:
        self.store = store
        self.bus = bus

    def publish_pending(self) -> int:
        count = 0
        for row in self.store.unacked():
            row.attempts += 1
            try:
                self.bus.publish(
                    row.topic,
                    {**row.payload, "idempotency_key": row.idempotency_key,
                     "event_id": row.event_id},
                )
            except Exception:
                continue  # leave un-acked; next scan retries
            row.published = True
            count += 1
        return count
