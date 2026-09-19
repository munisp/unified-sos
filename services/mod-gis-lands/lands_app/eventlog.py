"""Hash-chained cadastre event log (P1 audit immutability).

Every mutating cadastre operation — registration, subdivision, merger,
dispute lifecycle, title anchoring — appends an event to an append-only
hash chain: each event carries the ``event_hash`` of its predecessor, so any
tampering, deletion, or reordering is detected by :meth:`CadastreEventLog.verify`.

Uses the shared ``_shared.hashchain`` primitives (same import-guard idiom as
``main.py``'s observability wiring); container images that ship only the app
package fall back to identical local implementations.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from pydantic import BaseModel

log = logging.getLogger(__name__)

# --- AsyncAPI channels (contracts/asyncapi/platform-events.yaml) -------------
TOPIC_LANDS_TITLE_TRANSFERRED = "ng.sos.lands.title_transferred"
TOPIC_LANDS_TITLE_REVOKED = "ng.sos.lands.title_revoked"
TOPIC_LANDS_COMPENSATION_PAID = "ng.sos.lands.compensation_paid"


class LandsTitleTransferredEvent(BaseModel):
    """Payload for ``ng.sos.lands.title_transferred``."""

    state_id: str
    parcel_id: str
    instrument: str
    from_owner: str
    to_owner: str
    new_title_ref: str
    audit_hash: str
    timestamp: datetime


class LandsTitleRevokedEvent(BaseModel):
    """Payload for ``ng.sos.lands.title_revoked``."""

    state_id: str
    parcel_id: str
    public_purpose: str
    gazette_reference: Optional[str] = None
    audit_hash: str
    timestamp: datetime


class LandsCompensationPaidEvent(BaseModel):
    """Payload for ``ng.sos.lands.compensation_paid``."""

    state_id: str
    revocation_id: str
    claimant_id: str
    amount_kobo: int
    transfer_id: str
    timestamp: datetime


def _channel_payload(event: "CadastreEvent") -> tuple[str, BaseModel] | None:
    """Map a cadastre log event to its AsyncAPI channel payload (if any)."""
    detail = event.detail
    recorded = datetime.fromisoformat(event.recorded_at)
    if event.event_type == "TITLE_TRANSFERRED":
        return TOPIC_LANDS_TITLE_TRANSFERRED, LandsTitleTransferredEvent(
            state_id=event.tenant_state_id,
            parcel_id=str(event.parcel_id),
            instrument=str(detail.get("instrument_type", "")),
            from_owner=str(detail.get("from_owner_stin", "")),
            to_owner=str(detail.get("to_owner_stin", "")),
            new_title_ref=str(detail.get("new_c_of_o_number", "")),
            audit_hash=event.event_hash,
            timestamp=recorded,
        )
    if event.event_type == "TITLE_REVOKED":
        return TOPIC_LANDS_TITLE_REVOKED, LandsTitleRevokedEvent(
            state_id=event.tenant_state_id,
            parcel_id=str(event.parcel_id),
            public_purpose=str(detail.get("public_purpose", "")),
            gazette_reference=detail.get("gazette_reference"),
            audit_hash=event.event_hash,
            timestamp=recorded,
        )
    if event.event_type == "COMPENSATION_PAID":
        return TOPIC_LANDS_COMPENSATION_PAID, LandsCompensationPaidEvent(
            state_id=event.tenant_state_id,
            revocation_id=str(detail.get("revocation_id", "")),
            claimant_id=str(detail.get("claimant", "")),
            amount_kobo=int(detail.get("total_kobo", 0)),
            transfer_id=str(detail.get("hold_id", "")),
            timestamp=recorded,
        )
    return None

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
            body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
            body["prev_hash"] = prev_hash
            return hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
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
class CadastreEvent:
    """One immutable entry in the cadastre hash-chained event log."""

    event_id: str
    event_type: str
    tenant_state_id: str
    parcel_id: Optional[str]
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
            "parcel_id": self.parcel_id,
            "actor": self.actor,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }


class CadastreEventLog:
    """Append-only, tenant-scoped, hash-chained audit log (in-memory).

    Production wiring: mirrored to the ``cadastre.event_log`` table whose
    ``prev_hash``/``event_hash`` columns are maintained by an append-only
    trigger (no UPDATE/DELETE grants), exactly like this in-memory twin.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None, bus=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._bus = bus  # optional _shared.eventbus bus; fail-soft publish
        self._events: list[CadastreEvent] = []
        self._seq = itertools.count(1)

    def record(
        self,
        event_type: str,
        tenant_state_id: str,
        *,
        parcel_id: Optional[UUID | str] = None,
        actor: str = "lands-registry",
        detail: Optional[dict[str, Any]] = None,
    ) -> CadastreEvent:
        """Append an event, chained to the previous event's hash."""
        prev_hash = self._events[-1].event_hash if self._events else GENESIS_PREV_HASH
        payload: dict[str, Any] = {
            "event_id": f"cadastre-event-{next(self._seq):06d}",
            "event_type": event_type,
            "tenant_state_id": tenant_state_id,
            "parcel_id": str(parcel_id) if parcel_id is not None else None,
            "actor": actor,
            "detail": detail or {},
            "recorded_at": self._clock().isoformat(),
        }
        event = CadastreEvent(
            **payload,
            prev_hash=prev_hash,
            event_hash=event_payload_hash(payload, prev_hash),
        )
        self._events.append(event)
        self._publish(event)
        return event

    def _publish(self, event: CadastreEvent) -> None:
        """Fail-soft publish to the optional event bus (P1 event backbone).

        Only event types with a declared AsyncAPI channel are mapped; a bus
        outage must never break a cadastre mutation.
        """
        if self._bus is None:
            return
        mapped = _channel_payload(event)
        if mapped is None:
            return
        topic, payload = mapped
        try:
            self._bus.publish(topic, payload)
        except Exception:  # noqa: BLE001 - fail-soft by contract
            log.warning("event publish failed for %s", topic, exc_info=True)

    def events(
        self,
        tenant_state_id: Optional[str] = None,
        parcel_id: Optional[UUID | str] = None,
    ) -> list[CadastreEvent]:
        """Tenant-scoped (RLS-equivalent) read of the chain, optionally per parcel."""
        results = self._events
        if tenant_state_id is not None:
            results = [e for e in results if e.tenant_state_id == tenant_state_id]
        if parcel_id is not None:
            pid = str(parcel_id)
            results = [e for e in results if e.parcel_id == pid]
        return list(results)

    def verify(self) -> list[str]:
        """Recompute the whole chain; empty list means intact."""
        return verify_event_chain([e.as_dict() for e in self._events])
