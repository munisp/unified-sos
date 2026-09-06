"""mod-erp-bridge FastAPI application."""
from __future__ import annotations

import os
from datetime import date
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .adapters import AdapterUnavailableError
from .domain import (
    CoaMapping,
    JournalEntry,
    JournalLine,
    OutboundRecord,
    TENANT_STATES,
)
from .service import (
    ErpBridgeService,
    NotFoundError,
    UnknownTenantError,
    build_service,
)


class PushJournalRequest(BaseModel):
    """Journal push body; tenant comes from the path (no cross-tenant post)."""

    entry_id: str = Field(min_length=1)
    date: date
    memo: str = ""
    lines: List[JournalLine] = Field(min_length=2)
    source_event_id: str = Field(min_length=1)


class CoaMappingPut(BaseModel):
    mapping: Dict[str, str]


class JournalResponse(BaseModel):
    entry: JournalEntry
    record: OutboundRecord


class JournalDetailResponse(BaseModel):
    entry: JournalEntry
    receipt: Optional[object] = None


class OutboundVerifyResponse(BaseModel):
    valid: bool
    records: int
    errors: List[str]


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


def create_app(service: Optional[ErpBridgeService] = None) -> FastAPI:
    app = FastAPI(title="mod-erp-bridge — ERP Integration Bridge")
    app.state.service = service or build_service()
    if _instrument_fastapi is not None:
        _instrument_fastapi(app, service_name="mod-erp-bridge")

    def svc(request: Request) -> ErpBridgeService:
        return request.app.state.service

    def _map(exc: Exception):
        if isinstance(exc, UnknownTenantError):
            return HTTPException(404, str(exc))
        if isinstance(exc, NotFoundError):
            return HTTPException(404, str(exc))
        if isinstance(exc, AdapterUnavailableError):
            return HTTPException(503, str(exc))
        return HTTPException(500, str(exc))

    def _guard(state: str) -> None:
        if state not in TENANT_STATES:
            raise HTTPException(404, f"unknown tenant state {state!r}")

    # ---------------- health ----------------

    @app.get("/healthz")
    def healthz(request: Request):
        return svc(request).health()

    # ---------------- journals ----------------

    @app.post(
        "/erp/v1/states/{state}/journals",
        response_model=JournalResponse,
        status_code=201,
        operation_id="pushJournalEntry",
    )
    def push_journal(state: str, body: PushJournalRequest, request: Request):
        _guard(state)
        try:
            entry = JournalEntry(
                entry_id=body.entry_id,
                tenant_state_id=state,
                date=body.date,
                memo=body.memo,
                lines=body.lines,
                source_event_id=body.source_event_id,
            )
            record = svc(request).ingest(entry)
            return JournalResponse(entry=entry, record=record)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map(exc)

    @app.get(
        "/erp/v1/states/{state}/journals",
        response_model=List[JournalEntry],
        operation_id="listJournalEntries",
    )
    def list_journals(state: str, request: Request):
        _guard(state)
        return svc(request).list_journals(state)

    @app.get(
        "/erp/v1/states/{state}/journals/{entry_id}",
        response_model=JournalDetailResponse,
        operation_id="getJournalEntry",
    )
    def get_journal(state: str, entry_id: str, request: Request):
        _guard(state)
        try:
            service = svc(request)
            entry = service.get_journal(state, entry_id)
            receipt = service.receipt_for(state, entry_id)
            return JournalDetailResponse(entry=entry, receipt=receipt)
        except Exception as exc:  # noqa: BLE001
            raise _map(exc)

    # ---------------- COA mapping ----------------

    @app.get(
        "/erp/v1/states/{state}/coa-mapping",
        response_model=CoaMapping,
        operation_id="getCoaMapping",
    )
    def get_coa_mapping(state: str, request: Request):
        _guard(state)
        return svc(request).get_coa_mapping(state)

    @app.put(
        "/erp/v1/states/{state}/coa-mapping",
        response_model=CoaMapping,
        operation_id="putCoaMapping",
    )
    def put_coa_mapping(state: str, body: CoaMappingPut, request: Request):
        _guard(state)
        return svc(request).set_coa_mapping(state, body.mapping)

    # ---------------- outbound log ----------------

    @app.get(
        "/erp/v1/states/{state}/outbound-log/verify",
        response_model=OutboundVerifyResponse,
        operation_id="verifyOutboundLog",
    )
    def verify_outbound_log(state: str, request: Request):
        _guard(state)
        service = svc(request)
        errors = service.verify_outbound_log()
        return OutboundVerifyResponse(
            valid=not errors,
            records=len(service.outbound_log()),
            errors=errors,
        )

    return app


app = create_app() if os.environ.get("ERP_BRIDGE_NO_AUTOBUILD") != "1" else None
