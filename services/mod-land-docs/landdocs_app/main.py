"""FastAPI surface for mod-land-docs — land document management + OCR.

All endpoints live under ``/api/v1/states/{state_id}/land-docs/...`` and are
tenant-scoped by the ``X-State-Tenant`` header (400 when missing on /api/
routes, 400 when it disagrees with the path ``state_id``, 404 for
cross-tenant document access). ``/healthz`` and ``/metrics`` (Prometheus
counters) are unscoped. Fixture engines are the default; the production
profile fails closed without ``SOS_LANDDOCS_OCR_URL``.
"""

from __future__ import annotations

import base64
import logging
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path as _Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, generate_latest

from .adapters import (
    AdapterUnavailableError,
    DocumentClassifier,
    ocr_engine_from_env,
    object_store_from_env,
)
from .domain import (
    DocStatus,
    DocType,
    DuplicateWarning,
    IllegalTransitionError,
    LandDocument,
    LandDocsStore,
)
from .events import (
    EVENT_DOCUMENT_REGISTERED,
    EVENT_DOCUMENT_VERIFIED,
    EVENT_DUPLICATE_SUSPECTED,
    EVENT_OCR_COMPLETED,
    DocumentRegisteredEvent,
    DocumentVerifiedEvent,
    DuplicateSuspectedEvent,
    OcrCompletedEvent,
    publish,
)

# --- shared event bus (services/_shared/eventbus) -----------------------------
try:
    from _shared.eventbus import InMemoryEventBus, event_bus_from_env
except ImportError:
    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.eventbus import InMemoryEventBus, event_bus_from_env
    except ImportError:  # minimal container images ship only the app package
        InMemoryEventBus = None  # type: ignore[assignment]
        event_bus_from_env = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("mod-land-docs")

# --- Prometheus counters -------------------------------------------------------
DOCS_REGISTERED = Counter(
    "landdocs_documents_registered_total",
    "Land documents registered (new versions included).",
    labelnames=("tenant_state_id",),
)
OCR_RUNS = Counter(
    "landdocs_ocr_runs_total",
    "OCR extraction runs.",
    labelnames=("tenant_state_id", "engine"),
)
VERIFICATIONS = Counter(
    "landdocs_verifications_total",
    "Document verifications completed.",
    labelnames=("tenant_state_id",),
)


def get_store(request: Request) -> LandDocsStore:
    return request.app.state.store


def _tenant_path(request: Request) -> str:
    state_id = request.path_params.get("state_id")
    tenant = request.state.tenant
    if state_id is not None and state_id.lower() != tenant:
        raise HTTPException(
            status_code=400,
            detail="X-State-Tenant header must match the path state_id",
        )
    return tenant


def tenant_dependency(request: Request) -> str:
    return _tenant_path(request)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RegisterDocumentRequest(BaseModel):
    title: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    content_base64: str = Field(..., description="raw document bytes, base64")
    registered_by: str = Field(..., min_length=1)
    doc_type: DocType = DocType.OTHER
    parcel_id: Optional[str] = None


class RegisterDocumentResponse(BaseModel):
    document: LandDocument
    duplicate_warnings: list[DuplicateWarning]


class ActorRequest(BaseModel):
    actor: str = Field(..., min_length=1)


class VerifyRequest(BaseModel):
    verifier: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class RejectRequest(BaseModel):
    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class NewVersionRequest(BaseModel):
    filename: str = Field(..., min_length=1)
    content_base64: str
    actor: str = Field(..., min_length=1)


def _decode(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail="content_base64 is not valid base64")


def _bus_from_env_safe():
    if event_bus_from_env is None:
        return None
    try:
        return event_bus_from_env()
    except Exception:
        # fail-soft for the reference build: fall back to in-memory and keep
        # serving; production wiring sets EVENT_BUS explicitly.
        return InMemoryEventBus()


def create_app(
    store: Optional[LandDocsStore] = None,
    bus=None,
    environ: Optional[dict] = None,
) -> FastAPI:
    app = FastAPI(
        title="SOS mod-land-docs — Land Document Management + OCR",
        version="0.1.0",
        description="Land document registry: classification, OCR extraction, "
                    "verification, versioning, duplicate detection, hash-chained audit.",
    )
    if store is None:
        # fail-closed: AdapterUnavailableError propagates and boot aborts
        store = LandDocsStore(
            ocr_engine=ocr_engine_from_env(environ),
            object_store=object_store_from_env(environ),
            classifier=DocumentClassifier(),
        )
    app.state.store = store
    app.state.bus = bus if bus is not None else _bus_from_env_safe()

    @app.middleware("http")
    async def tenant_middleware(request: Request, call_next):
        """All /api/ routes require the X-State-Tenant header (fail-closed)."""
        if request.url.path.startswith("/api/"):
            header = request.headers.get("x-state-tenant")
            if not header:
                return PlainTextResponse(
                    '{"detail":"X-State-Tenant header is required"}',
                    status_code=400,
                    media_type="application/json",
                )
            request.state.tenant = header.lower()
        return await call_next(request)

    base = "/api/v1/states/{state_id}/land-docs"

    @app.post(f"{base}/documents", status_code=status.HTTP_201_CREATED,
              response_model=RegisterDocumentResponse, tags=["documents"])
    def register_document(
        req: RegisterDocumentRequest,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        content = _decode(req.content_base64)
        doc, warnings = store.register_document(
            tenant, req.title, req.filename, content, req.registered_by,
            doc_type=req.doc_type, parcel_id=req.parcel_id,
        )
        DOCS_REGISTERED.labels(tenant_state_id=tenant).inc()
        logger.info("document_registered tenant=%s doc=%s v=%d", tenant,
                    doc.document_id, doc.version)
        publish(app.state.bus, EVENT_DOCUMENT_REGISTERED, DocumentRegisteredEvent(
            tenant_state_id=tenant, document_id=doc.document_id, title=doc.title,
            doc_type=doc.doc_type.value, version=doc.version,
            content_hash=doc.content_hash, registered_by=doc.registered_by,
            registered_at=doc.registered_at,
        ))
        if warnings:
            publish(app.state.bus, EVENT_DUPLICATE_SUSPECTED, DuplicateSuspectedEvent(
                tenant_state_id=tenant, document_id=doc.document_id,
                suspected_of=[w.document_id for w in warnings],
                reasons=[w.reason for w in warnings], suspected_at=_now(),
            ))
        return RegisterDocumentResponse(document=doc, duplicate_warnings=warnings)

    def _doc_or_404(store: LandDocsStore, tenant: str, document_id: str):
        try:
            return store.get_document(tenant, document_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    def _transition_or_409(fn, *args):
        try:
            return fn(*args)
        except IllegalTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post(f"{base}/documents/{{document_id}}/classify",
              response_model=LandDocument, tags=["lifecycle"])
    def classify_document(
        document_id: str, req: ActorRequest,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        _doc_or_404(store, tenant, document_id)
        doc = _transition_or_409(store.classify_document, tenant, document_id, req.actor)
        logger.info("document_classified tenant=%s doc=%s type=%s", tenant,
                    document_id, doc.doc_type.value)
        return doc

    @app.post(f"{base}/documents/{{document_id}}/ocr",
              response_model=LandDocument, tags=["lifecycle"])
    def run_ocr(
        document_id: str, req: ActorRequest,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        _doc_or_404(store, tenant, document_id)
        doc = _transition_or_409(store.run_ocr, tenant, document_id, req.actor)
        engine = type(store.ocr_engine).__name__.replace("OcrEngine", "").lower()
        OCR_RUNS.labels(tenant_state_id=tenant, engine=engine).inc()
        logger.info("ocr_completed tenant=%s doc=%s confidence=%.2f", tenant,
                    document_id, doc.ocr_confidence or 0.0)
        publish(app.state.bus, EVENT_OCR_COMPLETED, OcrCompletedEvent(
            tenant_state_id=tenant, document_id=document_id, engine=engine,
            confidence=doc.ocr_confidence or 0.0,
            needs_manual_review=doc.needs_manual_review,
            parcel_id=doc.parcel_id, completed_at=_now(),
        ))
        return doc

    @app.post(f"{base}/documents/{{document_id}}/verify",
              response_model=LandDocument, tags=["lifecycle"])
    def verify_document(
        document_id: str, req: VerifyRequest,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        _doc_or_404(store, tenant, document_id)
        doc = _transition_or_409(store.verify_document, tenant, document_id,
                                 req.verifier, req.reason)
        VERIFICATIONS.labels(tenant_state_id=tenant).inc()
        logger.info("document_verified tenant=%s doc=%s by=%s", tenant,
                    document_id, req.verifier)
        publish(app.state.bus, EVENT_DOCUMENT_VERIFIED, DocumentVerifiedEvent(
            tenant_state_id=tenant, document_id=document_id,
            verified_by=req.verifier, reason=req.reason, verified_at=_now(),
        ))
        return doc

    @app.post(f"{base}/documents/{{document_id}}/reject",
              response_model=LandDocument, tags=["lifecycle"])
    def reject_document(
        document_id: str, req: RejectRequest,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        _doc_or_404(store, tenant, document_id)
        return _transition_or_409(store.reject_document, tenant, document_id,
                                  req.actor, req.reason)

    @app.post(f"{base}/documents/{{document_id}}/versions",
              status_code=status.HTTP_201_CREATED,
              response_model=LandDocument, tags=["versioning"])
    def add_version(
        document_id: str, req: NewVersionRequest,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        _doc_or_404(store, tenant, document_id)
        content = _decode(req.content_base64)
        doc = _transition_or_409(store.add_version, tenant, document_id,
                                 content, req.filename, req.actor)
        DOCS_REGISTERED.labels(tenant_state_id=tenant).inc()
        logger.info("document_versioned tenant=%s prior=%s new=%s v=%d", tenant,
                    document_id, doc.document_id, doc.version)
        return doc

    @app.get(f"{base}/documents", response_model=list[LandDocument],
             tags=["documents"])
    def list_documents(
        doc_type: Optional[DocType] = Query(default=None),
        status_filter: Optional[DocStatus] = Query(default=None, alias="status"),
        parcel_id: Optional[str] = Query(default=None),
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        return store.list_documents(tenant, doc_type=doc_type,
                                    status=status_filter, parcel_id=parcel_id)

    @app.get(f"{base}/documents/{{document_id}}", tags=["documents"])
    def get_document(
        document_id: str,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        doc = _doc_or_404(store, tenant, document_id)
        audit = store.document_audit(tenant, document_id)
        return {"document": doc, "audit_chain": audit["records"],
                "chain_intact": audit["chain_intact"]}

    @app.get(f"{base}/documents/{{document_id}}/audit", tags=["audit"])
    def document_audit(
        document_id: str,
        tenant: str = Depends(tenant_dependency),
        store: LandDocsStore = Depends(get_store),
    ):
        _doc_or_404(store, tenant, document_id)
        audit = store.document_audit(tenant, document_id)
        return {
            "document_id": audit["document_id"],
            "tenant_state_id": audit["tenant_state_id"],
            "entries": audit["entries"],
            "valid": audit["chain_intact"],
            "chain_errors": audit["chain_errors"],
            "records": audit["records"],
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest().decode(),
                                 media_type="text/plain; version=0.0.4")

    return app


app = create_app()
