"""Persistent processed-event store for consumer idempotency.

The settlement consumer dedupes on ``source_event_id``; an in-memory index
is lost on restart, so a replayed settlement would post a duplicate journal.
This module provides a durable seam:

* ``FileProcessedEventStore`` — durable JSON file fixture
  (``SOS_ERP_DEDUPE_FILE``), atomic replace per write.
* ``PostgresProcessedEventStore`` — production seam behind ``SOS_ERP_DSN``
  (import-guarded ``psycopg``; fail-closed without it).
* ``InMemoryProcessedEventStore`` — dev default only.

Fail-closed: the production profile raises without ``SOS_ERP_DSN``.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional, Protocol

from .adapters.base import AdapterUnavailableError


class ProcessedEventStore(Protocol):
    def lookup(self, state: str, source_event_id: str) -> Optional[str]:
        """Return the entry_id recorded for a processed event, if any."""
        ...

    def mark_processed(self, state: str, source_event_id: str, entry_id: str) -> None:
        """Persist the processed-event marker (same unit of work as the
        journal row recorded by the caller)."""
        ...


class InMemoryProcessedEventStore:
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()

    def lookup(self, state: str, source_event_id: str) -> Optional[str]:
        with self._lock:
            return self._data.get(state, {}).get(source_event_id)

    def mark_processed(self, state: str, source_event_id: str, entry_id: str) -> None:
        with self._lock:
            self._data.setdefault(state, {})[source_event_id] = entry_id


class FileProcessedEventStore:
    """Durable file-backed store (fixture): JSON mapping, atomic replace."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, str]] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text() or "{}")
            if isinstance(raw, dict):
                self._data = {str(k): dict(v) for k, v in raw.items()}

    def lookup(self, state: str, source_event_id: str) -> Optional[str]:
        with self._lock:
            return self._data.get(state, {}).get(source_event_id)

    def mark_processed(self, state: str, source_event_id: str, entry_id: str) -> None:
        with self._lock:
            self._data.setdefault(state, {})[source_event_id] = entry_id
            self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, sort_keys=True)
            os.replace(tmp, self.path)  # atomic on POSIX
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class PostgresProcessedEventStore:
    """Production seam: ``processed_events`` table keyed by
    (tenant_state_id, source_event_id). Fail-closed without SOS_ERP_DSN /
    the psycopg driver."""

    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or os.environ.get("SOS_ERP_DSN")
        if not self.dsn:
            raise AdapterUnavailableError(
                "processed-event store requires SOS_ERP_DSN (fail-closed)")
        try:  # pragma: no cover - optional dependency
            import psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterUnavailableError("psycopg package not installed") from exc
        self._psycopg = psycopg  # pragma: no cover

    def lookup(self, state: str, source_event_id: str) -> Optional[str]:  # pragma: no cover
        with self._psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT entry_id FROM processed_events "
                "WHERE tenant_state_id = %s AND source_event_id = %s",
                (state, source_event_id),
            ).fetchone()
        return row[0] if row else None

    def mark_processed(self, state: str, source_event_id: str, entry_id: str) -> None:  # pragma: no cover
        with self._psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO processed_events (tenant_state_id, source_event_id, entry_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (state, source_event_id, entry_id),
            )


def select_processed_event_store(
    profile: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> ProcessedEventStore:
    """DSN → Postgres; dedupe file → durable fixture; production without
    either fails closed; otherwise in-memory dev default."""
    env = dict(os.environ if env is None else env)
    profile = profile or env.get("SOS_PROFILE", "dev")
    if env.get("SOS_ERP_DSN"):
        return PostgresProcessedEventStore(env["SOS_ERP_DSN"])
    if env.get("SOS_ERP_DEDUPE_FILE"):
        return FileProcessedEventStore(env["SOS_ERP_DEDUPE_FILE"])
    if profile == "production":
        raise AdapterUnavailableError(
            "production profile requires SOS_ERP_DSN or SOS_ERP_DEDUPE_FILE "
            "for processed-event durability")
    return InMemoryProcessedEventStore()
