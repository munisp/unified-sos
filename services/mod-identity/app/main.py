"""mod-identity FastAPI application.

Production deployment note: consumer authentication is Keycloak (OIDC
client-credentials federation per state realm) and per-call metering/billing
is enforced at the APISIX gateway against this module's usage API — see
README.md.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .models import (
    ApiConsumer,
    AuditEntry,
    ConsentGrant,
    Credential,
    GuardianLink,
    Resident,
    ResidentRead,
    ResidentStatus,
    SettlementRecord,
    VerificationProduct,
    VerificationResult,
)
from .repo import IdentityRepository, InMemoryIdentityRepository
from .service import (
    ConsentError,
    GuardianshipError,
    IdentityService,
    InvalidTransitionError,
    NotFoundError,
    RegistrarRoleError,
    TenantIsolationError,
)


class VerifyRequest(BaseModel):
    state_id: str
    consumer_id: str
    resident_id: str
    product: VerificationProduct
    claim: str = ""


class ResidentStatusChange(BaseModel):
    state_id: str
    to_status: ResidentStatus
    actor_id: str
    actor_role: str = Field(description="must be 'registrar'")
    document_ref: Optional[str] = Field(
        default=None, description="death-certificate reference; required for DECEASED"
    )


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


def create_app(repo: Optional[IdentityRepository] = None) -> FastAPI:
    from .federation import FederationUnavailableError, build_federation_client

    app = FastAPI(title="mod-identity — Resident Registry & Governed Verification APIs")
    app.state.repo = repo or InMemoryIdentityRepository()
    # IDENTITY_FEDERATION_MODE=fixture (default, deterministic) | live
    # (per-state Keycloak client-credentials; boot fails listing missing env).
    app.state.federation_client = build_federation_client()

    def service(request: Request) -> IdentityService:
        return IdentityService(request.app.state.repo, request.app.state.federation_client)

    @app.post("/residents", response_model=ResidentRead, status_code=201)
    def register_resident(resident: Resident, svc: IdentityService = Depends(service)):
        return svc.register_resident(resident)

    @app.get("/residents/{resident_id}", response_model=ResidentRead)
    def get_resident(resident_id: str, request: Request, state_id: str):
        """Registry-operator path only — API consumers NEVER receive records."""
        resident = request.app.state.repo.get_resident(resident_id)
        if resident is None:
            raise HTTPException(404, f"resident {resident_id!r} not found")
        if resident.state_id != state_id:
            raise HTTPException(403, "cross-tenant access denied")
        return resident

    @app.post("/residents/{resident_id}/status", response_model=ResidentRead)
    def set_resident_status(resident_id: str, body: ResidentStatusChange,
                            svc: IdentityService = Depends(service)):
        """Registrar-only lifecycle change (DECEASED needs a death-certificate
        document reference); hash-chain audited."""
        try:
            return svc.set_resident_status(
                resident_id, body.state_id, body.to_status,
                body.actor_id, body.actor_role, body.document_ref,
            )
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))
        except RegistrarRoleError as exc:
            raise HTTPException(403, str(exc))
        except InvalidTransitionError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/guardian-links", response_model=GuardianLink, status_code=201)
    def add_guardian_link(link: GuardianLink, svc: IdentityService = Depends(service)):
        try:
            return svc.add_guardian_link(link)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))

    @app.post("/credentials", response_model=Credential, status_code=201)
    def issue_credential(credential: Credential, svc: IdentityService = Depends(service)):
        try:
            return svc.issue_credential(credential)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))

    @app.post("/consumers", response_model=ApiConsumer, status_code=201)
    def register_consumer(consumer: ApiConsumer, svc: IdentityService = Depends(service)):
        return svc.register_consumer(consumer)

    @app.post("/consents", response_model=ConsentGrant, status_code=201)
    def grant_consent(grant: ConsentGrant, svc: IdentityService = Depends(service)):
        try:
            return svc.grant_consent(grant)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))
        except GuardianshipError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/consents/{grant_id}/revoke", response_model=ConsentGrant)
    def revoke_consent(grant_id: str, state_id: str, svc: IdentityService = Depends(service)):
        try:
            return svc.revoke_consent(grant_id, state_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))

    @app.post("/verify", response_model=VerificationResult)
    def verify(body: VerifyRequest, svc: IdentityService = Depends(service)):
        try:
            return svc.verify(body.state_id, body.consumer_id, body.resident_id, body.product, body.claim)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))
        except ConsentError as exc:
            raise HTTPException(403, str(exc))
        except FederationUnavailableError as exc:
            raise HTTPException(503, f"REGISTRY_UNAVAILABLE: {exc}")

    @app.post("/settlements/{consumer_id}", response_model=SettlementRecord, status_code=201)
    def settle(consumer_id: str, state_id: str, svc: IdentityService = Depends(service)):
        try:
            return svc.settle_consumer(state_id, consumer_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/audit", response_model=list[AuditEntry])
    def audit(request: Request, state_id: Optional[str] = None):
        return request.app.state.repo.list_audit(state_id)

    @app.get("/audit/verify")
    def audit_integrity(svc: IdentityService = Depends(service)):
        return {"chain_valid": svc.verify_audit_chain()}

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-identity"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-identity")
    return app


app = create_app()
