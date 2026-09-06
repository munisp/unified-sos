"""mod-identity FastAPI application.

Production deployment note: consumer authentication is Keycloak (OIDC
client-credentials federation per state realm) and per-call metering/billing
is enforced at the APISIX gateway against this module's usage API — see
README.md.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .models import (
    ApiConsumer,
    AuditEntry,
    ConsentGrant,
    Credential,
    Resident,
    SettlementRecord,
    VerificationProduct,
    VerificationResult,
)
from .repo import IdentityRepository, InMemoryIdentityRepository
from .service import (
    ConsentError,
    IdentityService,
    NotFoundError,
    TenantIsolationError,
)


class VerifyRequest(BaseModel):
    state_id: str
    consumer_id: str
    resident_id: str
    product: VerificationProduct
    claim: str = ""


def create_app(repo: Optional[IdentityRepository] = None) -> FastAPI:
    app = FastAPI(title="mod-identity — Resident Registry & Governed Verification APIs")
    app.state.repo = repo or InMemoryIdentityRepository()

    def service(request: Request) -> IdentityService:
        return IdentityService(request.app.state.repo)

    @app.post("/residents", response_model=Resident, status_code=201)
    def register_resident(resident: Resident, svc: IdentityService = Depends(service)):
        return svc.register_resident(resident)

    @app.get("/residents/{resident_id}", response_model=Resident)
    def get_resident(resident_id: str, request: Request, state_id: str):
        """Registry-operator path only — API consumers NEVER receive records."""
        resident = request.app.state.repo.get_resident(resident_id)
        if resident is None:
            raise HTTPException(404, f"resident {resident_id!r} not found")
        if resident.state_id != state_id:
            raise HTTPException(403, "cross-tenant access denied")
        return resident

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

    return app


app = create_app()
