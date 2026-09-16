"""Hash-chained tenant audit log for mod-gis-luc (P1 audit immutability).

Mirrors the reference idiom (services/mod-gis-lands/lands_app/eventlog.py):
every key mutating operation appends an event to an append-only hash chain —
each event carries the ``event_hash`` of its predecessor, so tampering,
deletion, or reordering is detected by :meth:`AuditLog.verify`.

Uses the shared ``_shared.hashchain`` primitives (same import-guard idiom as
the service's observability wiring); container images that ship only the app
package fall back to identical local implementations.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from _shared.hashchain import (
        GENESIS_PREV_HASH,
        event_payload_hash,
        verify_event_chain,
    )
except ImportError:  # pragma: no cover - minimal container images
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.hashchain import (
            GENESIS_PREV_HASH,
            event_payload_hash,
            verify_event_chain,
        )
    except ImportError:  # local fallback, byte-identical semantics
        GENESIS_PREV_HASH = "0" * 64

        def event_payload_hash(payload: dict, prev_hash: str) -> str:
            body = {k: v for k, v in payload.items()
                    if k not in ("prev_hash", "event_hash")}
            body["prev_hash"] = prev_hash
            return hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":"),
                           default=str).encode("utf-8")
            ).hexdigest()

        def verify_event_chain(events) -> list[str]:
            errors: list[str] = []
            last_hash: Optional[str] = None
            for index, event in enumerate(events):
                label = event.get("event_id", f"index-{index}")
                prev = event.get("prev_hash")
                expected = GENESIS_PREV_HASH if last_hash is None else last_hash
                if prev != expected:
                    errors.append(f"event {label}: broken chain link")
                recorded = event.get("event_hash")
                if recorded != event_payload_hash(event, prev if prev is not None else ""):
                    errors.append(f"event {label}: hash mismatch — record tampered")
                last_hash = recorded
            return errors


@dataclass(frozen=True)
class AuditEvent:
    """One immutable entry in the hash-chained audit log."""

    event_id: str
    event_type: str
    tenant_state_id: str
    actor: str
    detail: dict[str, Any]
    recorded_at: str
    prev_hash: str
    event_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "tenant_state_id": self.tenant_state_id,
            "actor": self.actor,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }


class AuditLog:
    """Append-only, tenant-scoped, hash-chained audit log (in-memory).

    Production wiring: mirrored to an append-only audit table whose
    ``prev_hash``/``event_hash`` columns are maintained by an append-only
    trigger (no UPDATE/DELETE grants), exactly like this in-memory twin.
    """

    def __init__(self, prefix: str = "luc-audit") -> None:
        self._prefix = prefix
        self._events: list[AuditEvent] = []
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def record(
        self,
        event_type: str,
        tenant_state_id: str,
        *,
        actor: str = "mod-gis-luc",
        detail: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        """Append an event, chained to the previous event's hash."""
        with self._lock:
            prev_hash = (self._events[-1].event_hash if self._events
                         else GENESIS_PREV_HASH)
            payload: dict[str, Any] = {
                "event_id": f"{self._prefix}-{next(self._seq):06d}",
                "event_type": event_type,
                "tenant_state_id": tenant_state_id,
                "actor": actor,
                "detail": detail or {},
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            event = AuditEvent(
                **payload,
                prev_hash=prev_hash,
                event_hash=event_payload_hash(payload, prev_hash),
            )
            self._events.append(event)
            return event

    def events(self, tenant_state_id: Optional[str] = None) -> list[AuditEvent]:
        """Tenant-scoped (RLS-equivalent) read of the chain."""
        if tenant_state_id is None:
            return list(self._events)
        return [e for e in self._events if e.tenant_state_id == tenant_state_id]

    def verify(self) -> list[str]:
        """Recompute the whole chain; empty list means intact."""
        return verify_event_chain([e.as_dict() for e in self._events])
