"""Transactional outbox for mod-mortgage.

Guarantee: a domain transition (money moved, status changed) and its
``ng.sos.mortgage.*`` event row commit atomically inside
:class:`mortgage_app.domain.MortgageStore` — the API layer never publishes
directly. A relay (:class:`OutboxRelay`, background task or the explicit
``POST /internal/outbox/relay`` endpoint) publishes every un-acked row and
acks only on success, so a publish failure after a commit is healed on the
next relay pass (at-least-once; consumers dedupe on ``idempotency_key``).

Mirrors ``ledger/fundsflow/outbox.py``, implemented locally because
``ledger/`` is not importable from the service image.

Selection (environment-driven, fail-closed)::

    SOS_MORTGAGE_OUTBOX=fixture|postgres      (default: fixture)
    SOS_MORTGAGE_OUTBOX_DSN=postgresql://...  (required for postgres;
                                               required in production)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol
from uuid import uuid4

from .adapters import AdapterUnavailableError


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OutboxBus(Protocol):
    """Relay-side bus seam: publish a JSON-able dict payload."""

    def publish(self, topic: str, payload: Dict[str, Any]) -> None: ...


@dataclass
class OutboxRow:
    event_id: str
    topic: str
    payload: Dict[str, Any]
    idempotency_key: str
    created_at: str
    published: bool = False
    attempts: int = 0


class InMemoryOutbox:
    """Default outbox (dev/tests). Rows are appended by the domain store
    while it holds its lock, so the domain transition and the event row are
    committed atomically; a row is acked only after a successful publish."""

    def __init__(self, clock: Callable[[], str] = _utcnow_iso) -> None:
        self._clock = clock
        self.rows: List[OutboxRow] = []
        self._lock = threading.Lock()

    def append(self, topic: str, payload: Dict[str, Any], idempotency_key: str) -> OutboxRow:
        row = OutboxRow(
            event_id=f"evt-{uuid4().hex[:12]}",
            topic=topic,
            payload=dict(payload),
            idempotency_key=idempotency_key,
            created_at=self._clock(),
        )
        with self._lock:
            self.rows.append(row)
        return row

    def unacked(self) -> List[OutboxRow]:
        with self._lock:
            return [r for r in self.rows if not r.published]

    def ack(self, event_id: str) -> None:
        with self._lock:
            for r in self.rows:
                if r.event_id == event_id:
                    r.published = True
                    return
        raise KeyError(f"unknown outbox event {event_id}")


class PostgresOutbox:
    """Production outbox seam (``SOS_MORTGAGE_OUTBOX_DSN``) — fail-closed.

    Rows live in a ``mortgage_outbox`` table and are inserted in the same
    database transaction as the domain write. Constructing without a DSN
    (or without the ``psycopg`` driver) raises
    :class:`AdapterUnavailableError` rather than silently degrading to the
    in-memory outbox in production.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.dsn = dsn or env.get("SOS_MORTGAGE_OUTBOX_DSN")
        if not self.dsn:
            raise AdapterUnavailableError(
                "SOS_MORTGAGE_OUTBOX_DSN is required for PostgresOutbox "
                "(fail-closed: refusing to run without a durable outbox)"
            )
        try:
            import psycopg  # optional dependency  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "psycopg is required for PostgresOutbox (pip install 'psycopg[binary]')"
            ) from exc

    def _connect(self):  # pragma: no cover - network seam
        import psycopg

        return psycopg.connect(self.dsn)

    def append(self, topic: str, payload: Dict[str, Any], idempotency_key: str) -> OutboxRow:  # pragma: no cover
        import json

        row = OutboxRow(
            event_id=f"evt-{uuid4().hex[:12]}",
            topic=topic,
            payload=dict(payload),
            idempotency_key=idempotency_key,
            created_at=_utcnow_iso(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mortgage_outbox (event_id, topic, payload, idempotency_key,"
                " created_at, published, attempts) VALUES (%s,%s,%s,%s,%s,false,0)",
                (row.event_id, row.topic, json.dumps(row.payload),
                 row.idempotency_key, row.created_at),
            )
        return row

    def unacked(self) -> List[OutboxRow]:  # pragma: no cover
        import json

        with self._connect() as conn:
            cur = conn.execute(
                "SELECT event_id, topic, payload, idempotency_key, created_at, attempts"
                " FROM mortgage_outbox WHERE NOT published ORDER BY created_at"
            )
            return [
                OutboxRow(event_id=r[0], topic=r[1], payload=json.loads(r[2]),
                          idempotency_key=r[3], created_at=str(r[4]),
                          published=False, attempts=r[5])
                for r in cur.fetchall()
            ]

    def ack(self, event_id: str) -> None:  # pragma: no cover
        with self._connect() as conn:
            conn.execute(
                "UPDATE mortgage_outbox SET published = true WHERE event_id = %s",
                (event_id,),
            )


def outbox_from_env(environ: Optional[Dict[str, str]] = None):
    """Build an outbox from ``SOS_MORTGAGE_OUTBOX`` (default fixture)."""
    env = environ if environ is not None else dict(os.environ)
    kind = env.get("SOS_MORTGAGE_OUTBOX", "fixture")
    if kind == "fixture":
        return InMemoryOutbox()
    if kind == "postgres":
        return PostgresOutbox(environ=env)
    raise AdapterUnavailableError(f"unknown SOS_MORTGAGE_OUTBOX {kind!r}")


class OutboxRelay:
    """At-least-once relay with recovery scan.

    ``publish_pending`` IS the recovery scan: it publishes every un-acked
    row and acks only on success. A publish failure leaves the row
    un-acked, so the next relay pass (or the explicit
    ``POST /internal/outbox/relay`` endpoint) retries it. The domain effect
    itself is exactly-once (guarded by the store's idempotency keys);
    delivery is at-least-once and consumers dedupe on
    ``idempotency_key``/``event_id``.
    """

    def __init__(self, outbox, bus: OutboxBus) -> None:
        self.outbox = outbox
        self.bus = bus

    def publish_pending(self) -> int:
        count = 0
        for row in self.outbox.unacked():
            row.attempts += 1
            try:
                self.bus.publish(
                    row.topic,
                    {**row.payload, "idempotency_key": row.idempotency_key,
                     "event_id": row.event_id},
                )
            except Exception:
                continue  # leave un-acked; next scan retries
            self.outbox.ack(row.event_id)
            count += 1
        return count

    def publish_pending_best_effort(self) -> int:
        """Drain after a request; failures are retried by a later pass."""
        try:
            return self.publish_pending()
        except Exception:
            return 0
