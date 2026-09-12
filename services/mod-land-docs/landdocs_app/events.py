"""Event-bus topics + payloads for mod-land-docs.

Follows the services/_shared/eventbus in-process bus idiom used by
mod-safecity-vision: publish Pydantic payloads on well-known topics; the
orchestrator wires durable topics (AsyncAPI contract intent).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

EVENT_DOCUMENT_REGISTERED = "ng.sos.landdocs.document_registered"
EVENT_OCR_COMPLETED = "ng.sos.landdocs.ocr_completed"
EVENT_DOCUMENT_VERIFIED = "ng.sos.landdocs.document_verified"
EVENT_DUPLICATE_SUSPECTED = "ng.sos.landdocs.duplicate_suspected"


class DocumentRegisteredEvent(BaseModel):
    tenant_state_id: str
    document_id: str
    title: str
    doc_type: str
    version: int
    content_hash: str
    registered_by: str
    registered_at: str


class OcrCompletedEvent(BaseModel):
    tenant_state_id: str
    document_id: str
    engine: str
    confidence: float
    needs_manual_review: bool
    parcel_id: Optional[str] = None
    completed_at: str


class DocumentVerifiedEvent(BaseModel):
    tenant_state_id: str
    document_id: str
    verified_by: str
    reason: str
    verified_at: str


class DuplicateSuspectedEvent(BaseModel):
    tenant_state_id: str
    document_id: str
    suspected_of: List[str]
    reasons: List[str]
    suspected_at: str


def publish(bus, topic: str, payload: BaseModel) -> None:
    """Publish on the shared bus (fail-soft: no bus, no-op)."""
    if bus is not None:
        bus.publish(topic, payload)
