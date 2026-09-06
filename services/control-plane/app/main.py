"""FastAPI application implementing contracts/openapi/control-plane.yaml.

Endpoints beyond the two contracted ones (GET tenants, suspend, audit feed)
support the tenant-lifecycle workflow and observability; they carry metadata
only, consistent with the zero-PII data boundary.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

from .domain import (
    MetadataStore,
    PolicyPackRecord,
    PolicyPackRef,
    Tenant,
    TenantCreate,
    TenantOperation,
    TenantStatus,
)
from .pii_guard import PiiGuardMiddleware
from .policy import validate_policy_pack


class SuspendRequest(BaseModel):
    reason: str


def get_store(request: Request) -> MetadataStore:
    return request.app.state.store


def actor(request: Request) -> str:
    """Actor identity from the bearer token subject (dev fallback)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").split(".")[0] or "bearer"
    return "anonymous"


def create_app(store: MetadataStore | None = None) -> FastAPI:
    app = FastAPI(
        title="SOS Control Plane — Tenant Provisioning API",
        version="1.0.0",
        description="WP-01 / EPIC-01. Metadata only — zero citizen PII (PII guard enforced).",
    )
    app.state.store = store or MetadataStore()
    app.add_middleware(PiiGuardMiddleware)

    @app.post(
        "/control/v1/tenants",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TenantOperation,
    )
    def create_tenant(req: TenantCreate, store: MetadataStore = Depends(get_store),
                      who: str = Depends(actor)) -> TenantOperation:
        """Provision a new state tenant (equivalent to `sosctl tenant create`)."""
        tenant = store.create_tenant(req, actor=who)
        return TenantOperation(
            tenant_id=tenant.tenant_id,
            status=tenant.status,
            provisioned_resources=tenant.provisioned_resources,
        )

    @app.get("/control/v1/tenants", response_model=list[Tenant])
    def list_tenants(store: MetadataStore = Depends(get_store)) -> list[Tenant]:
        return store.list_tenants()

    @app.get("/control/v1/tenants/{tenant_id}", response_model=Tenant)
    def get_tenant(tenant_id: str, store: MetadataStore = Depends(get_store)) -> Tenant:
        tenant = store.get_tenant(tenant_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found")
        return tenant

    @app.post("/control/v1/tenants/{tenant_id}/suspend", response_model=Tenant)
    def suspend_tenant(tenant_id: str, req: SuspendRequest,
                       store: MetadataStore = Depends(get_store),
                       who: str = Depends(actor)) -> Tenant:
        tenant = store.suspend_tenant(tenant_id, req.reason, actor=who)
        if tenant is None:
            raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found")
        return tenant

    @app.post("/control/v1/tenants/{tenant_id}/policy-packs",
              response_model=PolicyPackRecord)
    def ingest_policy_pack(tenant_id: str, ref: PolicyPackRef, response: Response,
                           store: MetadataStore = Depends(get_store),
                           who: str = Depends(actor)) -> PolicyPackRecord:
        """Ingest a dynamic state policy pack (schema + guardrail validated)."""
        if store.get_tenant(tenant_id) is None:
            raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found")
        errors = validate_policy_pack(ref.document)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "policy pack failed schema/guardrail validation", "errors": errors},
            )
        record = store.activate_policy_pack(tenant_id, ref, actor=who)
        assert record is not None
        return record

    @app.get("/control/v1/tenants/{tenant_id}/policy-packs",
             response_model=list[PolicyPackRecord])
    def list_policy_packs(tenant_id: str,
                          store: MetadataStore = Depends(get_store)) -> list[PolicyPackRecord]:
        if store.get_tenant(tenant_id) is None:
            raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' not found")
        return store.list_policy_packs(tenant_id)

    @app.get("/control/v1/audit-events")
    def audit_events(store: MetadataStore = Depends(get_store)) -> dict:
        """Append-only audit feed (production: OpenSearch immutable archive, 7y)."""
        events = store.audit_events()
        return {"count": len(events), "events": [e.model_dump() for e in events]}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
