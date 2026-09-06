"""mod-agri-waybill FastAPI application."""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .models import (
    EWaybill,
    TrackingEvent,
    VerificationResult,
    WarehouseReceipt,
)
from .service import AgriWaybillService, InvalidTransition, NotFoundError


class VerifyRequest(BaseModel):
    qr_payload: str


def create_app(service: Optional[AgriWaybillService] = None) -> FastAPI:
    app = FastAPI(title="mod-agri-waybill — Agribusiness Supply Chain & E-Waybill")
    app.state.service = service or AgriWaybillService()

    def svc(request: Request) -> AgriWaybillService:
        return request.app.state.service

    def _map(exc: Exception):
        if isinstance(exc, NotFoundError):
            return HTTPException(404, str(exc))
        return HTTPException(409, str(exc))

    @app.post("/waybills", response_model=EWaybill, status_code=201)
    def issue_waybill(waybill: EWaybill, request: Request):
        try:
            return svc(request).issue_waybill(waybill)
        except InvalidTransition as exc:
            raise _map(exc)

    @app.get("/waybills/{waybill_number}", response_model=EWaybill)
    def get_waybill(waybill_number: str, request: Request):
        try:
            return svc(request).get_waybill(waybill_number)
        except NotFoundError as exc:
            raise _map(exc)

    @app.post("/waybills/verify", response_model=VerificationResult)
    def verify(body: VerifyRequest, request: Request):
        """Checkpoint QR verification (target < 10 s; in-process ≈ ms)."""
        return svc(request).verify_qr(body.qr_payload)

    @app.post("/waybills/{waybill_number}/tracking", response_model=TrackingEvent, status_code=201)
    def track(waybill_number: str, event: TrackingEvent, request: Request):
        if event.waybill_number != waybill_number:
            raise HTTPException(422, "path/body waybill_number mismatch")
        try:
            return svc(request).record_checkpoint(event)
        except (NotFoundError, InvalidTransition) as exc:
            raise _map(exc)

    @app.get("/waybills/{waybill_number}/tracking", response_model=List[TrackingEvent])
    def trail(waybill_number: str, request: Request):
        try:
            return svc(request).tracking_trail(waybill_number)
        except NotFoundError as exc:
            raise _map(exc)

    @app.post("/waybills/{waybill_number}/deliver", response_model=EWaybill)
    def deliver(waybill_number: str, request: Request):
        try:
            return svc(request).mark_delivered(waybill_number)
        except (NotFoundError, InvalidTransition) as exc:
            raise _map(exc)

    @app.post("/warehouse-receipts", response_model=WarehouseReceipt, status_code=201)
    def issue_receipt(receipt: WarehouseReceipt, request: Request):
        try:
            return svc(request).issue_receipt(receipt)
        except (NotFoundError, InvalidTransition) as exc:
            raise _map(exc)

    @app.get("/warehouse-receipts", response_model=List[WarehouseReceipt])
    def list_receipts(request: Request, warehouse_id: Optional[str] = None):
        return svc(request).list_receipts(warehouse_id)

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-agri-waybill"}

    return app


app = create_app()
