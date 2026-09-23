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
import os
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

    def publish_batch(self, events: List[tuple]) -> None:
        """Batch publish: one bus call for a seq-ordered chunk of events.

        Same at-least-once / fail_next semantics as ``publish`` — the whole
        batch raises on an injected failure so the relay falls back to
        per-row isolation.
        """
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected bus publish failure")
        self.published.extend(events)


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


DEFAULT_MAX_ATTEMPTS = 8
OUTBOX_DEADLETTER_TOPIC = "ng.sos.fundsflow.outbox_deadletter"

POSTGRES_OUTBOX_DDL = """\
CREATE TABLE IF NOT EXISTS ledger_outbox (
    event_id        TEXT PRIMARY KEY,
    seq             BIGSERIAL,
    topic           TEXT NOT NULL,
    payload         JSONB NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at      DOUBLE PRECISION NOT NULL,
    published       BOOLEAN NOT NULL DEFAULT FALSE,
    attempts        INTEGER NOT NULL DEFAULT 0,
    dead            BOOLEAN NOT NULL DEFAULT FALSE
);
"""


@dataclass
class OutboxRow:
    event_id: str
    topic: str
    payload: Dict[str, Any]
    idempotency_key: str
    created_at: float
    published: bool = False
    attempts: int = 0
    seq: int = 0  # monotonic per-store ordering; assigned on commit
    dead: bool = False  # dead-lettered after max_attempts; skipped by relay


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
        self._seq = 0
        self.crash_before_commit: bool = False  # crash-injection hook

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def transaction(self) -> "InMemoryOutboxTransaction":
        return InMemoryOutboxTransaction(self)

    def unacked(self) -> List[OutboxRow]:
        """Unpublished, non-dead rows in seq (commit) order."""
        return sorted(
            (r for r in self.outbox if not r.published and not r.dead),
            key=lambda r: r.seq,
        )


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
        for row in self._events:
            row.seq = self._store.next_seq()  # monotonic commit order
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

    def write_batch(
        self,
        entries: List[Dict[str, Any]],
    ) -> List[OutboxRow]:
        """Write many (domain_record, topic, event_payload, idempotency_key)
        entries in ONE store transaction — one commit for the whole batch
        instead of one per event. Atomicity is unchanged: a crash before the
        single commit loses all rows, never a partial flush.

        Each entry is a dict with keys ``domain_record``, ``topic``,
        ``event_payload`` and ``idempotency_key``.
        """
        now = self.store._clock()
        rows = [
            OutboxRow(
                event_id=str(uuid.uuid4()),
                topic=e["topic"],
                payload=e["event_payload"],
                idempotency_key=e["idempotency_key"],
                created_at=now,
            )
            for e in entries
        ]
        tx = self.store.transaction()
        for e, row in zip(entries, rows):
            tx.write_domain(e["domain_record"])
            tx.write_event(row)
        tx.commit()
        return rows


class OutboxRelay:
    """At-least-once relay with recovery scan.

    ``publish_pending`` IS the recovery scan: it publishes every un-acked
    row and acks only on success. Re-running it after a crash republishes
    rows that were committed but never published. Consumers must dedupe on
    ``idempotency_key`` (at-least-once ⇒ duplicates are possible).
    """

    def __init__(
        self,
        store: InMemoryOutboxStore,
        bus: EventBusProtocol,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.store = store
        self.bus = bus
        self.max_attempts = max_attempts

    @staticmethod
    def _envelope(row: OutboxRow) -> Dict[str, Any]:
        return {**row.payload, "idempotency_key": row.idempotency_key,
                "event_id": row.event_id}

    def _publish_row(self, row: OutboxRow) -> bool:
        """Publish one row with ack-on-success / dead-letter-on-exhaustion.

        Returns True when the row was published (acked)."""
        row.attempts += 1
        try:
            self.bus.publish(row.topic, self._envelope(row))
        except Exception:
            if row.attempts >= self.max_attempts:
                self._dead_letter(row)
            return False  # leave un-acked; next scan retries (unless dead)
        row.published = True
        return True

    def publish_pending(self) -> int:
        count = 0
        for row in self.store.unacked():  # seq order; dead rows excluded
            if self._publish_row(row):
                count += 1
        return count

    def publish_pending_batched(self, batch_size: int = 64) -> int:
        """Batched flush: one ``bus.publish_batch`` call per seq-ordered
        chunk instead of one publish per row (fewer bus round-trips on the
        relay hot path).

        Semantics are identical to :meth:`publish_pending`: seq ordering is
        preserved, rows ack only on success, and a failed batch falls back
        to per-row isolation so a single poisoned event cannot block its
        chunk-mates (and dead-letter accounting stays per-row). Buses
        without ``publish_batch`` use the per-row path directly.
        """
        rows = self.store.unacked()
        if not rows:
            return 0
        publish_batch = getattr(self.bus, "publish_batch", None)
        if publish_batch is None or batch_size <= 1:
            return self._publish_rows_individually(rows)
        count = 0
        for start in range(0, len(rows), batch_size):
            chunk = rows[start:start + batch_size]
            try:
                publish_batch([(r.topic, self._envelope(r)) for r in chunk])
            except Exception:
                count += self._publish_rows_individually(chunk)
                continue
            for r in chunk:
                r.attempts += 1
                r.published = True
            count += len(chunk)
        return count

    def _publish_rows_individually(self, rows: List[OutboxRow]) -> int:
        count = 0
        for row in rows:
            if self._publish_row(row):
                count += 1
        return count

    def _dead_letter(self, row: OutboxRow) -> None:
        """Mark a row dead after max_attempts and alert on the bus.

        Dead rows are skipped by future publish scans; an operator must
        intervene. The dead-letter event itself is best-effort on the bus.
        """
        row.dead = True
        try:
            self.bus.publish(
                OUTBOX_DEADLETTER_TOPIC,
                {
                    "event_id": row.event_id,
                    "topic": row.topic,
                    "idempotency_key": row.idempotency_key,
                    "attempts": row.attempts,
                },
            )
        except Exception:
            pass  # the row is dead regardless; reconciler will catch it


class AdapterUnavailableError(RuntimeError):
    pass


class PostgresOutboxStore:
    """Fail-closed Postgres outbox seam (SOS_FUNDSFLOW_OUTBOX_DSN).

    DDL: :data:`POSTGRES_OUTBOX_DDL` (ledger_outbox with BIGSERIAL seq for
    monotonic per-store ordering and a dead flag for dead-lettered rows).
    Uses psycopg (sync) or asyncpg — either driver satisfies the seam. In
    the production profile (SOS_FUNDSFLOW_PROFILE=production) a missing DSN
    raises; the in-memory fixture remains the default elsewhere.
    """

    DDL = POSTGRES_OUTBOX_DDL

    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or os.environ.get("SOS_FUNDSFLOW_OUTBOX_DSN")
        if not self.dsn:
            raise AdapterUnavailableError(
                "Postgres outbox store requires SOS_FUNDSFLOW_OUTBOX_DSN (fail-closed)"
            )
        self._driver = None
        try:  # pragma: no cover - optional dependency
            import psycopg  # type: ignore  # noqa: F401
            self._driver = "psycopg"
        except ImportError:
            try:  # pragma: no cover
                import asyncpg  # type: ignore  # noqa: F401
                self._driver = "asyncpg"
            except ImportError as exc:  # pragma: no cover
                raise AdapterUnavailableError(
                    "neither psycopg nor asyncpg installed for Postgres outbox"
                ) from exc


def select_outbox_store(profile: Optional[str] = None):
    """Environment-driven store selection (fail-closed in production)."""
    profile = profile or os.environ.get("SOS_FUNDSFLOW_PROFILE", "dev")
    if os.environ.get("SOS_FUNDSFLOW_OUTBOX_DSN"):
        return PostgresOutboxStore()
    if profile == "production":
        raise AdapterUnavailableError(
            "production profile requires SOS_FUNDSFLOW_OUTBOX_DSN; "
            "refusing in-memory outbox"
        )
    return InMemoryOutboxStore()
