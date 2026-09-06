"""Batch sync engine.

Pushes pending outbox records to the APISIX gateway on reconnect.

- **Transport**: ``httpx.Client`` — production uses mutual TLS; pass
  ``cert=("device.crt", "device.key")`` and ``verify="/path/to/ca-bundle.pem"``
  to :class:`SyncEngine` (mTLS hooks). Tests inject an in-process
  ``httpx.ASGITransport`` against the fake gateway.
- **Retries**: exponential backoff with full jitter
  (``sleep = random(0, min(base * 2**attempt, cap))``).
- **Idempotency**: the dedupe key ``(device_id, sequence)`` is enforced
  server-side; the client only marks records synced on an explicit
  ``accepted``/``duplicate`` ack, so re-sending after a crash is safe.
- **Resumability**: pending state lives in the SQLite outbox; a restarted
  daemon constructs a new SyncEngine over the same DB and continues.
- **Event backbone bridge**: when ``bus`` is provided (or
  :func:`sync_engine_from_env` selects one via ``EVENT_BUS``), every
  gateway-acknowledged record is also published as its AsyncAPI payload onto
  the configured bus (Kafka central tier / Fluvio edge tier). Unset
  ``EVENT_BUS`` keeps the pure outbox→gateway behaviour (default).
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

from .models import RecordKind, SyncBatch, SyncBatchResult
from .outbox import Outbox

_SHARED = Path(__file__).resolve().parents[3] / "services" / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from eventbus import EventBus, event_bus_from_env  # noqa: E402

#: Default record-kind → AsyncAPI channel map for the event backbone bridge.
#: Only channels present in the generated registry
#: (contracts/asyncapi/registry/topics.json) may be used; operators extend
#: this with ``EVENT_EDGE_CHANNELS`` (JSON object) as contracts add channels.
DEFAULT_EVENT_CHANNELS: Dict[str, str] = {
    RecordKind.REVENUE_TICKET.value: "ng.sos.payments.settlement_completed",
}


def sync_engine_from_env(outbox: Outbox, **kwargs) -> "SyncEngine":
    """Build a SyncEngine, wiring an event bus when ``EVENT_BUS`` is set.

    Fail-closed via the shared eventbus factory: ``EVENT_BUS=kafka`` requires
    ``EVENT_KAFKA_BOOTSTRAP``; ``EVENT_BUS=fluvio`` requires the fluvio client
    on the edge device. Unset ``EVENT_BUS`` returns a gateway-only engine.
    Optional ``EVENT_EDGE_CHANNELS`` (JSON object) overrides the default
    record-kind → channel map.
    """
    bus: Optional[EventBus] = None
    if os.environ.get("EVENT_BUS"):
        bus = event_bus_from_env()
    channels = dict(DEFAULT_EVENT_CHANNELS)
    override = os.environ.get("EVENT_EDGE_CHANNELS")
    if override:
        channels.update(json.loads(override))
    return SyncEngine(outbox, bus=bus, event_channels=channels, **kwargs)


class SyncEngine:
    def __init__(
        self,
        outbox: Outbox,
        gateway_url: str = "http://gateway.sos.local",
        batch_size: int = 500,
        max_retries: int = 5,
        backoff_base_s: float = 0.25,
        backoff_cap_s: float = 8.0,
        timeout_s: float = 5.0,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        cert: object = None,
        verify: object = True,
        sleep: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
        bus: Optional[EventBus] = None,
        event_channels: Optional[Dict[str, str]] = None,
    ) -> None:
        self.outbox = outbox
        self.gateway_url = gateway_url.rstrip("/")
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self.bus = bus
        self.event_channels = dict(
            DEFAULT_EVENT_CHANNELS if event_channels is None else event_channels
        )
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._client = httpx.Client(
            transport=transport,
            cert=cert,  # mTLS client certificate hook
            verify=verify,  # CA bundle for server verification
            timeout=timeout_s,
        )

    def _backoff_delay(self, attempt: int) -> float:
        cap = min(self.backoff_base_s * (2**attempt), self.backoff_cap_s)
        return self._rng.uniform(0, cap)

    def _post_batch(self, batch: SyncBatch) -> SyncBatchResult:
        resp = self._client.post(
            f"{self.gateway_url}/edge/v1/sync",
            json=batch.model_dump(mode="json"),
        )
        resp.raise_for_status()
        return SyncBatchResult.model_validate(resp.json())

    def sync_once(self) -> int:
        """Push one batch. Returns the number of records acknowledged."""
        records = self.outbox.pending(limit=self.batch_size)
        if not records:
            return 0
        batch = SyncBatch(device_id=self.outbox.device_id, records=records)

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                result = self._post_batch(batch)
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise SyncFailedError(
                        f"gateway push failed after {self.max_retries + 1} attempts"
                    ) from exc
                self._sleep(self._backoff_delay(attempt))
        else:  # pragma: no cover - loop always breaks or raises
            raise SyncFailedError("unreachable") from last_error

        acked = [
            ack.sequence
            for ack in result.acks
            if ack.status in ("accepted", "duplicate")
        ]
        if self.bus is not None and acked:
            # Event backbone bridge: emit acknowledged records as their
            # AsyncAPI payloads before marking them synced, so a publish
            # failure leaves the record pending and the next sync retries
            # (server-side dedupe on (device_id, sequence) makes the
            # re-push safe). Records with no channel mapping are skipped.
            by_sequence = {r.sequence: r for r in records}
            for seq in acked:
                record = by_sequence[seq]
                channel = self.event_channels.get(record.payload.kind.value)
                if channel is not None:
                    self.bus.publish(channel, record.payload)
        # 'rejected' records stay pending for operator investigation; they are
        # NOT deleted (audit trail), and a poison record never blocks the rest
        # of the queue because the gateway acks each record independently.
        self.outbox.mark_synced(acked)
        return len(acked)

    def sync_all(self, max_batches: int = 100) -> int:
        """Drain the outbox. Returns total acknowledged records."""
        total = 0
        for _ in range(max_batches):
            n = self.sync_once()
            total += n
            if n == 0:
                break
        return total

    def close(self) -> None:
        self._client.close()


class SyncFailedError(RuntimeError):
    """Gateway unreachable after all retries; records remain pending."""
