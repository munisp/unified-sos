"""mod-environment FastAPI application (ENV-09)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from .domain import (
    CarbonCredit,
    CarbonProject,
    DeforestationAlert,
    EIAApplication,
    EIAStatus,
    Permit,
    TelemetryEvaluation,
    TelemetryReading,
)
from .repository import InvalidTenantError, NotFoundError
from .service import EnvironmentService, InvalidTransition


class TransferRequest(BaseModel):
    tenant_state_id: str
    new_owner_id: str


class EIAAdvanceRequest(BaseModel):
    tenant_state_id: str
    target: EIAStatus
    decision_reason: Optional[str] = None


def create_app(service: Optional[EnvironmentService] = None) -> FastAPI:
    app = FastAPI(
        title="mod-environment — Environmental Protection, Carbon Registry & Industrial Emissions"
    )
    app.state.service = service or EnvironmentService()

    def svc(request: Request) -> EnvironmentService:
        return request.app.state.service

    def handle(exc: Exception):
        if isinstance(exc, NotFoundError):
            raise HTTPException(404, str(exc))
        if isinstance(exc, InvalidTenantError):
            raise HTTPException(422, str(exc))
        if isinstance(exc, (InvalidTransition, ValueError)):
            raise HTTPException(409, str(exc))
        raise exc

    # -- telemetry & compliance --------------------------------------------------

    @app.post(
        "/environment/v1/telemetry", response_model=TelemetryEvaluation, status_code=201
    )
    def ingest_telemetry(reading: TelemetryReading, request: Request):
        try:
            return svc(request).ingest_telemetry(reading)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.get("/environment/v1/facilities/{facility_id}/compliance")
    def facility_compliance(
        facility_id: str, request: Request, state_id: str = Query(...)
    ):
        try:
            return svc(request).facility_compliance(state_id, facility_id)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    # -- permits & levies ---------------------------------------------------------

    @app.post("/environment/v1/permits", response_model=Permit, status_code=201)
    def create_permit(permit: Permit, request: Request):
        try:
            return svc(request).create_permit(permit)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post("/environment/v1/permits/{permit_id}/activate", response_model=Permit)
    def activate_permit(
        permit_id: str, request: Request, state_id: str = Query(...)
    ):
        try:
            return svc(request).activate_permit(state_id, permit_id)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    # -- deforestation surveillance -------------------------------------------------

    @app.post(
        "/environment/v1/deforestation-alerts",
        response_model=DeforestationAlert,
        status_code=201,
    )
    def raise_alert(alert: DeforestationAlert, request: Request):
        try:
            return svc(request).raise_deforestation_alert(alert)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post(
        "/environment/v1/deforestation-alerts/{alert_id}/dispatch",
        response_model=DeforestationAlert,
    )
    def dispatch_alert(alert_id: str, request: Request, state_id: str = Query(...)):
        try:
            return svc(request).dispatch_alert(state_id, alert_id)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    # -- carbon registry --------------------------------------------------------------

    @app.post(
        "/environment/v1/carbon-projects", response_model=CarbonProject, status_code=201
    )
    def register_project(project: CarbonProject, request: Request):
        try:
            return svc(request).register_project(project)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post("/environment/v1/carbon-credits", response_model=CarbonCredit, status_code=201)
    def create_credit(credit: CarbonCredit, request: Request):
        try:
            return svc(request).create_credit(credit)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post(
        "/environment/v1/carbon-credits/{credit_id}/issue", response_model=CarbonCredit
    )
    def issue_credit(credit_id: str, request: Request, state_id: str = Query(...)):
        try:
            return svc(request).issue_credit(state_id, credit_id)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post("/environment/v1/carbon-credits/{credit_id}/transfer")
    def transfer_credit(credit_id: str, body: TransferRequest, request: Request):
        try:
            return svc(request).transfer_credit(
                body.tenant_state_id, credit_id, body.new_owner_id
            )
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post(
        "/environment/v1/carbon-credits/{credit_id}/retire", response_model=CarbonCredit
    )
    def retire_credit(credit_id: str, request: Request, state_id: str = Query(...)):
        try:
            return svc(request).retire_credit(state_id, credit_id)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    # -- EIA workflow -----------------------------------------------------------------

    @app.post("/environment/v1/eias", response_model=EIAApplication, status_code=201)
    def submit_eia(application: EIAApplication, request: Request):
        try:
            return svc(request).submit_eia(application)
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.post(
        "/environment/v1/eias/{application_id}/advance", response_model=EIAApplication
    )
    def advance_eia(
        application_id: str, body: EIAAdvanceRequest, request: Request
    ):
        try:
            return svc(request).advance_eia(
                body.tenant_state_id, application_id, body.target, body.decision_reason
            )
        except Exception as exc:  # noqa: BLE001
            handle(exc)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "module": "mod-environment"}

    return app


app = create_app()
