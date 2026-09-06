"""mod-forestry FastAPI application."""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .models import (
    DeforestationAlert,
    ProvenanceEvent,
    StumpageInvoice,
    TimberTag,
    UntaggedTimberAlert,
)
from .service import ForestryService, InvalidTransition, NotFoundError


class UntaggedHaulageReport(BaseModel):
    state_id: str
    checkpoint_id: str
    vehicle_plate: str
    gps: dict


# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None


def create_app(service: Optional[ForestryService] = None) -> FastAPI:
    app = FastAPI(title="mod-forestry — Timber Provenance & Deforestation Alerts")
    app.state.service = service or ForestryService()

    def svc(request: Request) -> ForestryService:
        return request.app.state.service

    @app.post("/tags", response_model=TimberTag, status_code=201)
    def register_tag(tag: TimberTag, request: Request):
        try:
            return svc(request).register_tag(tag)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/tags", response_model=List[TimberTag])
    def list_tags(request: Request, state_id: Optional[str] = None):
        return svc(request).list_tags(state_id)

    @app.get("/tags/{tag_id}", response_model=TimberTag)
    def get_tag(tag_id: str, request: Request):
        try:
            return svc(request).get_tag(tag_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/tags/{tag_id}/provenance", response_model=List[ProvenanceEvent])
    def provenance(tag_id: str, request: Request):
        try:
            return svc(request).provenance_chain(tag_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/provenance", response_model=ProvenanceEvent, status_code=201)
    def record_provenance(event: ProvenanceEvent, request: Request):
        try:
            return svc(request).record_provenance(event)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except (InvalidTransition, ValueError) as exc:
            raise HTTPException(409, str(exc))

    @app.post("/tags/{tag_id}/stumpage", response_model=StumpageInvoice, status_code=201)
    def bill_stumpage(tag_id: str, request: Request):
        try:
            return svc(request).bill_stumpage(tag_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/invoices", response_model=List[StumpageInvoice])
    def invoices(request: Request, tag_id: Optional[str] = None):
        return svc(request).list_invoices(tag_id)

    @app.post("/alerts/deforestation", response_model=DeforestationAlert, status_code=201)
    def ingest_deforestation_alert(alert: DeforestationAlert, request: Request):
        return svc(request).ingest_alert(alert)

    @app.get("/alerts/deforestation", response_model=List[DeforestationAlert])
    def list_alerts(
        request: Request,
        state_id: Optional[str] = None,
        actionable_only: bool = False,
    ):
        return svc(request).list_alerts(state_id, actionable_only)

    @app.post(
        "/alerts/untagged-haulage", response_model=UntaggedTimberAlert, status_code=201
    )
    def untagged_haulage(report: UntaggedHaulageReport, request: Request):
        return svc(request).report_untagged_haulage(**report.model_dump())

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-forestry"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-forestry")
    return app


app = create_app()
