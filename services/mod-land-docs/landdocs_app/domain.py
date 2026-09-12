"""Domain: land document lifecycle, versioning, duplicate detection, audit.

All state is tenant-scoped (``X-State-Tenant``). Deterministic fixture
engines run by default; production engines live behind the fail-closed
adapters in ``adapters.py``. Every state transition is appended to a
hash-chained audit log (services/_shared/hashchain, P1 audit immutability).
"""

from __future__ import annotations

import difflib
import hashlib
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .adapters import (
    DocumentClassifier,
    FixtureObjectStore,
    ObjectStoreAdapter,
    OcrEngine,
)

# --- hash-chained audit: reuse services/_shared/hashchain, local fallback ----
try:
    from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash, verify_event_chain
except ImportError:  # minimal container images ship only the app package
    import json

    GENESIS_PREV_HASH = "0" * 64

    def event_payload_hash(payload, prev_hash):  # type: ignore[no-redef]
        body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
        body["prev_hash"] = prev_hash
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def verify_event_chain(events):  # type: ignore[no-redef]
        errors: list[str] = []
        last = None
        for i, event in enumerate(events):
            prev = event.get("prev_hash")
            expected = GENESIS_PREV_HASH if last is None else last
            if prev != expected:
                errors.append(f"event {i}: broken chain link")
            if event.get("event_hash") != event_payload_hash(event, prev or ""):
                errors.append(f"event {i}: hash mismatch — record tampered")
            last = event.get("event_hash")
        return errors


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class IllegalTransitionError(ValueError):
    """Document lifecycle transition not allowed from the current status."""


class CrossTenantError(ValueError):
    """Tenant-isolation violation — mapped to HTTP 404 at the API layer."""


class DocStatus(str, Enum):
    REGISTERED = "REGISTERED"
    CLASSIFIED = "CLASSIFIED"
    OCR_EXTRACTED = "OCR_EXTRACTED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"
    SUPERSEDED = "SUPERSEDED"


class DocType(str, Enum):
    DEED = "DEED"
    SURVEY_PLAN = "SURVEY_PLAN"
    C_OF_O = "C_OF_O"
    CONSENT_LETTER = "CONSENT_LETTER"
    TAX_CLEARANCE = "TAX_CLEARANCE"
    IDENTITY = "IDENTITY"
    OTHER = "OTHER"


#: Allowed lifecycle transitions (new versions supersede any non-terminal doc).
_ALLOWED: Dict[DocStatus, tuple[DocStatus, ...]] = {
    DocStatus.REGISTERED: (DocStatus.CLASSIFIED,),
    DocStatus.CLASSIFIED: (DocStatus.OCR_EXTRACTED,),
    DocStatus.OCR_EXTRACTED: (DocStatus.VERIFIED, DocStatus.REJECTED),
    DocStatus.VERIFIED: (DocStatus.ARCHIVED,),
    DocStatus.REJECTED: (),
    DocStatus.ARCHIVED: (),
    DocStatus.SUPERSEDED: (),
}

DUPLICATE_TITLE_RATIO = 0.9
MANUAL_REVIEW_CONFIDENCE = 0.7


class DuplicateWarning(BaseModel):
    document_id: str
    title: str
    reason: str  # "identical_content_hash" | "similar_title"
    score: float = Field(..., ge=0.0, le=1.0)


class LandDocument(BaseModel):
    document_id: str
    tenant_state_id: str
    title: str
    filename: str
    doc_type: DocType = DocType.OTHER
    status: DocStatus = DocStatus.REGISTERED
    version: int = Field(default=1, ge=1)
    parcel_id: Optional[str] = None
    content_hash: str
    storage_ref: str
    root_document_id: str  # version lineage anchor (== document_id for v1)
    superseded_by: Optional[str] = None
    classifier_scores: Dict[str, int] = Field(default_factory=dict)
    extracted_fields: Dict[str, str] = Field(default_factory=dict)
    ocr_confidence: Optional[float] = None
    needs_manual_review: bool = False
    verified_by: Optional[str] = None
    verification_reason: Optional[str] = None
    rejection_reason: Optional[str] = None
    registered_by: str
    registered_at: str
    updated_at: str


class AuditRecord(BaseModel):
    event_id: str
    document_id: str
    tenant_state_id: str
    action: str
    actor: str
    detail: str
    recorded_at: str
    prev_hash: str
    event_hash: str


class _TenantScope:
    def __init__(self) -> None:
        self.documents: Dict[str, LandDocument] = {}
        self.audit: List[dict] = []


class LandDocsStore:
    """In-memory, tenant-scoped land document store (reference build)."""

    def __init__(
        self,
        ocr_engine: OcrEngine,
        object_store: Optional[ObjectStoreAdapter] = None,
        classifier: Optional[DocumentClassifier] = None,
    ) -> None:
        self.ocr_engine = ocr_engine
        self.object_store = object_store or FixtureObjectStore()
        self.classifier = classifier or DocumentClassifier()
        self._lock = threading.Lock()
        self._scopes: Dict[str, _TenantScope] = {}

    # -- tenant plumbing ------------------------------------------------------
    def _scope(self, tenant: str) -> _TenantScope:
        with self._lock:
            if tenant not in self._scopes:
                self._scopes[tenant] = _TenantScope()
            return self._scopes[tenant]

    def _doc(self, tenant: str, document_id: str) -> LandDocument:
        doc = self._scope(tenant).documents.get(document_id)
        if doc is None:
            raise KeyError(f"document '{document_id}' not found")
        return doc

    # -- audit ----------------------------------------------------------------
    def _audit(self, scope: _TenantScope, doc: LandDocument, action: str,
               actor: str, detail: str) -> None:
        """Append a state transition to the hash-chained audit log."""
        with self._lock:
            prev = scope.audit[-1]["event_hash"] if scope.audit else GENESIS_PREV_HASH
            record = {
                "event_id": _id("aud"),
                "document_id": doc.document_id,
                "tenant_state_id": doc.tenant_state_id,
                "action": action,
                "actor": actor,
                "detail": detail,
                "recorded_at": _now(),
                "prev_hash": prev,
            }
            record["event_hash"] = event_payload_hash(record, prev)
            scope.audit.append(record)

    # -- duplicate detection --------------------------------------------------
    def find_duplicates(self, tenant: str, title: str,
                        content_hash: str) -> List[DuplicateWarning]:
        """Exact SHA-256 content-hash match + fuzzy title match (difflib)."""
        warnings: List[DuplicateWarning] = []
        norm = " ".join(title.lower().split())
        for other in self._scope(tenant).documents.values():
            if other.content_hash == content_hash:
                warnings.append(DuplicateWarning(
                    document_id=other.document_id, title=other.title,
                    reason="identical_content_hash", score=1.0,
                ))
                continue
            ratio = difflib.SequenceMatcher(
                None, norm, " ".join(other.title.lower().split())
            ).ratio()
            if ratio >= DUPLICATE_TITLE_RATIO:
                warnings.append(DuplicateWarning(
                    document_id=other.document_id, title=other.title,
                    reason="similar_title", score=round(ratio, 4),
                ))
        return warnings

    # -- lifecycle ------------------------------------------------------------
    def register_document(
        self,
        tenant: str,
        title: str,
        filename: str,
        content: bytes,
        registered_by: str,
        doc_type: DocType = DocType.OTHER,
        parcel_id: Optional[str] = None,
    ) -> tuple[LandDocument, List[DuplicateWarning]]:
        content_hash = hashlib.sha256(content).hexdigest()
        duplicates = self.find_duplicates(tenant, title, content_hash)
        doc_id = _id("doc")
        storage_ref = self.object_store.put(f"{tenant}/{doc_id}/v1/{filename}", content)
        now = _now()
        doc = LandDocument(
            document_id=doc_id,
            tenant_state_id=tenant,
            title=title,
            filename=filename,
            doc_type=doc_type,
            status=DocStatus.REGISTERED,
            version=1,
            parcel_id=parcel_id,
            content_hash=content_hash,
            storage_ref=storage_ref,
            root_document_id=doc_id,
            registered_by=registered_by,
            registered_at=now,
            updated_at=now,
        )
        self._scope(tenant).documents[doc.document_id] = doc
        self._audit(self._scope(tenant), doc, "REGISTERED", registered_by,
                    f"registered '{title}' ({filename}, sha256:{content_hash[:12]}…)")
        return doc, duplicates

    def _transition(self, tenant: str, document_id: str, target: DocStatus,
                    actor: str, detail: str) -> LandDocument:
        doc = self._doc(tenant, document_id)
        if target not in _ALLOWED[doc.status]:
            raise IllegalTransitionError(
                f"cannot transition document '{document_id}' from "
                f"{doc.status.value} to {target.value}"
            )
        updated = doc.model_copy(update={"status": target, "updated_at": _now()})
        self._scope(tenant).documents[document_id] = updated
        self._audit(self._scope(tenant), updated, target.value, actor, detail)
        return updated

    def classify_document(self, tenant: str, document_id: str, actor: str) -> LandDocument:
        doc = self._doc(tenant, document_id)
        text = "\n".join(doc.extracted_fields.values())
        doc_type, scores = self.classifier.classify(doc.filename, text)
        updated = self._transition(tenant, document_id, DocStatus.CLASSIFIED, actor,
                                   f"classified as {doc_type}")
        updated = updated.model_copy(update={
            "doc_type": DocType(doc_type),
            "classifier_scores": scores,
        })
        self._scope(tenant).documents[document_id] = updated
        return updated

    def run_ocr(self, tenant: str, document_id: str, actor: str) -> LandDocument:
        doc = self._doc(tenant, document_id)
        content = self.object_store.get(doc.storage_ref)
        result = self.ocr_engine.extract(content, doc.filename)
        needs_review = result.confidence < MANUAL_REVIEW_CONFIDENCE
        updated = self._transition(
            tenant, document_id, DocStatus.OCR_EXTRACTED, actor,
            f"ocr engine={result.engine} confidence={result.confidence:.2f}"
            + (" — low confidence, manual review" if needs_review else ""),
        )
        updated = updated.model_copy(update={
            "extracted_fields": result.fields,
            "ocr_confidence": result.confidence,
            "needs_manual_review": needs_review,
            # OCR may refine the parcel id declared at registration.
            "parcel_id": doc.parcel_id or result.fields.get("parcel_id"),
        })
        self._scope(tenant).documents[document_id] = updated
        return updated

    def verify_document(self, tenant: str, document_id: str, verifier: str,
                        reason: str) -> LandDocument:
        if not verifier:
            raise ValueError("verification requires a verifier actor")
        if not reason:
            raise ValueError("verification requires a reason")
        updated = self._transition(tenant, document_id, DocStatus.VERIFIED, verifier,
                                   f"verified: {reason}")
        updated = updated.model_copy(update={
            "verified_by": verifier,
            "verification_reason": reason,
        })
        self._scope(tenant).documents[document_id] = updated
        return updated

    def reject_document(self, tenant: str, document_id: str, actor: str,
                        reason: str) -> LandDocument:
        if not reason:
            raise ValueError("rejection requires a reason")
        updated = self._transition(tenant, document_id, DocStatus.REJECTED, actor,
                                   f"rejected: {reason}")
        updated = updated.model_copy(update={"rejection_reason": reason})
        self._scope(tenant).documents[document_id] = updated
        return updated

    def archive_document(self, tenant: str, document_id: str, actor: str) -> LandDocument:
        return self._transition(tenant, document_id, DocStatus.ARCHIVED, actor,
                                "archived")

    # -- versioning -------------------------------------------------------------
    def add_version(
        self,
        tenant: str,
        document_id: str,
        content: bytes,
        filename: str,
        actor: str,
    ) -> LandDocument:
        """Upload a new version: the prior head is SUPERSEDED, the new version
        restarts the lifecycle at REGISTERED with a monotonic version number."""
        prior = self._doc(tenant, document_id)
        if prior.status in (DocStatus.SUPERSEDED,):
            raise IllegalTransitionError(
                f"document '{document_id}' is already superseded; version the head"
            )
        content_hash = hashlib.sha256(content).hexdigest()
        new_id = _id("doc")
        version = prior.version + 1
        storage_ref = self.object_store.put(
            f"{tenant}/{new_id}/v{version}/{filename}", content
        )
        superseded = prior.model_copy(update={
            "status": DocStatus.SUPERSEDED,
            "superseded_by": new_id,
            "updated_at": _now(),
        })
        scope = self._scope(tenant)
        scope.documents[document_id] = superseded
        self._audit(scope, superseded, DocStatus.SUPERSEDED.value, actor,
                    f"superseded by {new_id} (v{version})")
        now = _now()
        new_doc = LandDocument(
            document_id=new_id,
            tenant_state_id=tenant,
            title=prior.title,
            filename=filename,
            doc_type=prior.doc_type,
            status=DocStatus.REGISTERED,
            version=version,
            parcel_id=prior.parcel_id,
            content_hash=content_hash,
            storage_ref=storage_ref,
            root_document_id=prior.root_document_id,
            registered_by=actor,
            registered_at=now,
            updated_at=now,
        )
        scope.documents[new_id] = new_doc
        self._audit(scope, new_doc, "REGISTERED", actor,
                    f"registered v{version} of '{prior.title}' "
                    f"(supersedes {document_id})")
        return new_doc

    # -- queries ----------------------------------------------------------------
    def get_document(self, tenant: str, document_id: str) -> LandDocument:
        return self._doc(tenant, document_id)

    def list_documents(
        self,
        tenant: str,
        doc_type: Optional[DocType] = None,
        status: Optional[DocStatus] = None,
        parcel_id: Optional[str] = None,
    ) -> List[LandDocument]:
        docs = list(self._scope(tenant).documents.values())
        if doc_type is not None:
            docs = [d for d in docs if d.doc_type == doc_type]
        if status is not None:
            docs = [d for d in docs if d.status == status]
        if parcel_id is not None:
            docs = [d for d in docs if d.parcel_id == parcel_id]
        return docs

    def document_audit(self, tenant: str, document_id: str) -> dict:
        self._doc(tenant, document_id)
        records = [r for r in self._scope(tenant).audit
                   if r["document_id"] == document_id]
        return {
            "document_id": document_id,
            "tenant_state_id": tenant,
            "entries": len(records),
            "chain_intact": not verify_event_chain(records),
            "chain_errors": verify_event_chain(records),
            "records": records,
        }
