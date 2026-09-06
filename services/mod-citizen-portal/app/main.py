"""mod-citizen-portal FastAPI application (CIT-11).

Production deployment note: citizen authentication is Keycloak (OIDC,
one realm per state tenant — see the ``keycloak_realm`` wallet field) and
the durable payroll clean-up workflow runs on Temporal; this module exposes
the portal APIs and records workflow references only. See README.md.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .channels.gateway_seam import verify_shared_secret
from .channels.ivr import IvrChannelAdapter
from .channels.ussd import UssdChannelAdapter
from .domain import hash_msisdn

from .domain import (
    BiometricVerification,
    CivilServant,
    IdentityWallet,
    PayrollAudit,
    Petition,
    PetitionStatus,
    Priority,
    RequestStatus,
    ServiceCatalogEntry,
    ServiceRequest,
    SsoSession,
)
from .repository import CitizenPortalRepository, InMemoryCitizenPortalRepository
from .service import (
    CitizenPortalService,
    InvalidTransitionError,
    NotFoundError,
    TenantIsolationError,
    WalletSuspendedError,
)


class WalletCreate(BaseModel):
    state_id: str
    nin: str = Field(description="raw NIN — hashed immediately, never stored")


class WalletRead(BaseModel):
    """Read model: masked NIN only — structurally cannot leak the raw NIN."""

    wallet_id: str
    state_id: str
    masked_nin: str
    keycloak_realm: str
    keycloak_client_id: str
    status: str

    @classmethod
    def from_wallet(cls, wallet: IdentityWallet) -> "WalletRead":
        return cls(
            wallet_id=wallet.wallet_id,
            state_id=wallet.state_id,
            masked_nin=wallet.masked_nin(),
            keycloak_realm=wallet.keycloak_realm,
            keycloak_client_id=wallet.keycloak_client_id,
            status=wallet.status.value,
        )


class SsoSessionCreate(BaseModel):
    state_id: str
    wallet_id: str
    redirect_uri: str
    scopes: Optional[List[str]] = None


class ServiceRequestCreate(BaseModel):
    state_id: str
    wallet_id: str
    service_code: str
    form_payload: Dict[str, str] = Field(default_factory=dict)
    priority: Priority = Priority.STANDARD


class AdvanceRequest(BaseModel):
    state_id: str
    to_status: RequestStatus
    note: str = ""


class PetitionCreate(BaseModel):
    state_id: str
    wallet_id: str
    title: str
    body: str


class PetitionAdvance(BaseModel):
    state_id: str
    to_status: PetitionStatus
    note: str = ""


class BiometricVerificationCreate(BaseModel):
    state_id: str
    employee_no: str
    liveness_passed: bool
    verified: bool


class PayrollAuditCreate(BaseModel):
    state_id: str


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


def create_app(repo: Optional[CitizenPortalRepository] = None) -> FastAPI:
    app = FastAPI(title="mod-citizen-portal — Unified Citizen Portal, SSO & Civil Service Clean-Up")
    app.state.repo = repo or InMemoryCitizenPortalRepository()

    def service(request: Request) -> CitizenPortalService:
        return CitizenPortalService(request.app.state.repo)

    def guard(exc: Exception) -> HTTPException:
        if isinstance(exc, NotFoundError):
            return HTTPException(404, str(exc))
        if isinstance(exc, TenantIsolationError):
            return HTTPException(403, str(exc))
        if isinstance(exc, (InvalidTransitionError, WalletSuspendedError)):
            return HTTPException(409, str(exc))
        return HTTPException(400, str(exc))

    # -- wallets / SSO -----------------------------------------------------
    @app.post("/citizen/v1/wallets", response_model=WalletRead, status_code=201)
    def create_wallet(body: WalletCreate, svc: CitizenPortalService = Depends(service)):
        return WalletRead.from_wallet(svc.create_wallet(body.state_id, body.nin))

    @app.get("/citizen/v1/wallets/{wallet_id}", response_model=WalletRead)
    def get_wallet(wallet_id: str, state_id: str, svc: CitizenPortalService = Depends(service)):
        try:
            return WalletRead.from_wallet(svc.get_wallet(wallet_id, state_id))
        except (NotFoundError, TenantIsolationError) as exc:
            raise guard(exc)

    @app.post("/citizen/v1/sso/sessions", response_model=SsoSession, status_code=201)
    def create_sso_session(body: SsoSessionCreate, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.create_sso_session(body.state_id, body.wallet_id, body.redirect_uri, body.scopes)
        except (NotFoundError, TenantIsolationError, WalletSuspendedError) as exc:
            raise guard(exc)

    # -- service catalog / requests -----------------------------------------
    @app.get("/citizen/v1/services", response_model=List[ServiceCatalogEntry])
    def list_services(state_id: str, svc: CitizenPortalService = Depends(service)):
        return svc.ensure_catalog(state_id)

    @app.post("/citizen/v1/service-requests", response_model=ServiceRequest, status_code=201)
    def submit_service_request(body: ServiceRequestCreate, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.submit_service_request(
                body.state_id, body.wallet_id, body.service_code, body.form_payload, body.priority
            )
        except (NotFoundError, TenantIsolationError) as exc:
            raise guard(exc)

    @app.get("/citizen/v1/service-requests/{request_id}", response_model=ServiceRequest)
    def get_service_request(request_id: str, state_id: str, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.get_service_request(request_id, state_id)
        except (NotFoundError, TenantIsolationError) as exc:
            raise guard(exc)

    @app.post("/citizen/v1/service-requests/{request_id}/advance", response_model=ServiceRequest)
    def advance_service_request(request_id: str, body: AdvanceRequest, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.advance_service_request(request_id, body.state_id, body.to_status, body.note)
        except (NotFoundError, TenantIsolationError, InvalidTransitionError) as exc:
            raise guard(exc)

    # -- petitions -------------------------------------------------------------
    @app.post("/citizen/v1/petitions", response_model=Petition, status_code=201)
    def submit_petition(body: PetitionCreate, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.submit_petition(body.state_id, body.wallet_id, body.title, body.body)
        except (NotFoundError, TenantIsolationError) as exc:
            raise guard(exc)

    @app.post("/citizen/v1/petitions/{petition_id}/advance", response_model=Petition)
    def advance_petition(petition_id: str, body: PetitionAdvance, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.advance_petition(petition_id, body.state_id, body.to_status, body.note)
        except (NotFoundError, TenantIsolationError, InvalidTransitionError) as exc:
            raise guard(exc)

    # -- civil-service clean-up ---------------------------------------------------
    @app.post("/citizen/v1/civil-servants", response_model=CivilServant, status_code=201)
    def register_civil_servant(servant: CivilServant, svc: CitizenPortalService = Depends(service)):
        return svc.register_civil_servant(servant)

    @app.post("/citizen/v1/biometric-verifications", response_model=BiometricVerification, status_code=201)
    def record_biometric_verification(body: BiometricVerificationCreate, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.record_biometric_verification(
                body.state_id, body.employee_no, body.liveness_passed, body.verified
            )
        except NotFoundError as exc:
            raise guard(exc)

    @app.post("/citizen/v1/payroll-audits", response_model=PayrollAudit, status_code=201)
    def run_payroll_audit(body: PayrollAuditCreate, svc: CitizenPortalService = Depends(service)):
        return svc.run_payroll_audit(body.state_id)

    @app.get("/citizen/v1/payroll-audits/{audit_id}", response_model=PayrollAudit)
    def get_payroll_audit(audit_id: str, state_id: str, svc: CitizenPortalService = Depends(service)):
        try:
            return svc.get_payroll_audit(audit_id, state_id)
        except (NotFoundError, TenantIsolationError) as exc:
            raise guard(exc)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "module": "mod-citizen-portal"}

    # -- citizen channels (USSD / IVR telco webhooks) ---------------------------
    # Fail-closed auth: every callback must carry the shared telco secret in
    # the X-Telco-Secret header; when CITIZEN_PORTAL_TELCO_SECRET is unset the
    # endpoints reject all traffic. Raw MSISDNs are hashed at the edge and
    # never reach the session store.

    TELCO_SECRET_HEADER = "x-telco-secret"

    def _check_telco_secret(request: Request) -> None:
        expected = os.environ.get("CITIZEN_PORTAL_TELCO_SECRET", "")
        if not verify_shared_secret(request.headers.get(TELCO_SECRET_HEADER, ""), expected):
            raise HTTPException(403, "invalid or missing telco shared secret")

    async def _webhook_payload(request: Request) -> Dict[str, str]:
        """Africa's Talking posts form-encoded; generic gateways may post JSON."""
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            return {str(k): str(v) for k, v in (body or {}).items()}
        form = await request.form()
        return {str(k): str(v) for k, v in form.items()}

    async def _channel_callback(
        request: Request,
        adapter,
        state_id: str,
    ) -> PlainTextResponse:
        _check_telco_secret(request)
        payload = await _webhook_payload(request)
        session_id = payload.get("sessionId") or payload.get("session_id") or ""
        phone = payload.get("phoneNumber") or payload.get("phone_number") or ""
        text = payload.get("text", "")
        if not session_id or not phone:
            raise HTTPException(400, "sessionId and phoneNumber are required")
        response = adapter.handle_session(
            state_id=state_id,
            session_id=session_id,
            msisdn_hash=hash_msisdn(phone),
            input_text=text.split("*")[-1],  # AT sends the full USSD input chain
        )
        prefix = "END " if response.end_session else "CON "
        return PlainTextResponse(prefix + response.text)

    @app.post("/channels/ussd/callback", response_class=PlainTextResponse)
    async def ussd_callback(
        request: Request,
        state_id: str,
        svc: CitizenPortalService = Depends(service),
    ):
        return await _channel_callback(request, UssdChannelAdapter(svc), state_id)

    @app.post("/channels/ivr/callback", response_class=PlainTextResponse)
    async def ivr_callback(
        request: Request,
        state_id: str,
        locale: str = "en",
        svc: CitizenPortalService = Depends(service),
    ):
        return await _channel_callback(request, IvrChannelAdapter(svc, locale=locale), state_id)

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-citizen-portal")
    return app


app = create_app()
