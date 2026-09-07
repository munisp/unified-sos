"""FastAPI application implementing contracts/openapi/control-plane.yaml.

Endpoints beyond the two contracted ones (GET tenants, suspend, audit feed)
support the tenant-lifecycle workflow and observability; they carry metadata
only, consistent with the zero-PII data boundary.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

from .branding import Branding, BrandingRegistry
from .domain import (
    MetadataStore,
    PolicyPackRecord,
    PolicyPackRef,
    Tenant,
    TenantCreate,
    TenantOperation,
    TenantStatus,
)
from .operators.base import OperatorUnavailableError
from .pii_guard import PiiGuardMiddleware
from .policy import validate_policy_pack

#: Env vars required by each live operator. Boot is fail-closed: with
#: CONTROL_PLANE_OPERATORS=live the app refuses to start and lists exactly
#: which variables are missing.
_LIVE_REQUIRED_ENV: dict[str, tuple[str, ...]] = {
    "namespace": ("KUBECONFIG", "K8S_IN_CLUSTER"),  # either one
    "postgres": ("CP_PG_DSN",),
    "keycloak": ("KEYCLOAK_ADMIN_URL", "KEYCLOAK_ADMIN_USER", "KEYCLOAK_ADMIN_PASSWORD"),
    "s3": ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"),
    "kms": ("KMS_BACKEND", "KMS_VAULT_ADDR", "KMS_VAULT_TOKEN"),
}


def build_operators() -> dict:
    """Build the operator set from CONTROL_PLANE_OPERATORS (local|live).

    Live mode is fail-closed: a missing dependency or config var raises
    ``OperatorUnavailableError`` naming exactly what is absent.
    """
    mode = os.environ.get("CONTROL_PLANE_OPERATORS", "local")
    if mode == "local":
        from .operators import local_operators

        return local_operators()
    if mode != "live":
        raise OperatorUnavailableError(
            f"invalid CONTROL_PLANE_OPERATORS '{mode}' (expected local|live)"
        )

    missing: list[str] = []
    for step, vars_ in _LIVE_REQUIRED_ENV.items():
        if step == "namespace":  # kubeconfig OR in-cluster
            if not (os.environ.get("KUBECONFIG") or
                    os.environ.get("K8S_IN_CLUSTER") == "true"):
                missing.append("KUBECONFIG or K8S_IN_CLUSTER=true")
            continue
        missing.extend(v for v in vars_ if not os.environ.get(v))
    if missing:
        raise OperatorUnavailableError(
            "CONTROL_PLANE_OPERATORS=live missing required configuration: "
            + ", ".join(sorted(missing))
        )

    from .operators.k8s_namespace import K8sNamespaceOperator
    from .operators.keycloak_realm import KeycloakOperator
    from .operators.kms_keyring import KmsOperator
    from .operators.postgres_schema import PostgresOperator
    from .operators.s3_bucket import S3Operator

    kms = KmsOperator()
    return {
        "namespace": K8sNamespaceOperator(
            kubeconfig=os.environ.get("KUBECONFIG") or None,
            in_cluster=os.environ.get("K8S_IN_CLUSTER") == "true",
        ),
        "postgres": PostgresOperator(dsn=os.environ["CP_PG_DSN"]),
        "keycloak": KeycloakOperator(),
        "s3": S3Operator(kms_key_id=os.environ.get("S3_KMS_KEY_ID") or None),
        "kms": kms,
    }


class SuspendRequest(BaseModel):
    reason: str


class DomainVerifyRequest(BaseModel):
    custom_domain: str


def get_store(request: Request) -> MetadataStore:
    return request.app.state.store


def actor(request: Request) -> str:
    """Actor identity from the bearer token subject (dev fallback)."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").split(".")[0] or "bearer"
    return "anonymous"


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


def require_admin(request: Request) -> str:
    """Admin gate for mutating branding endpoints.

    The token comes from ``SOS_CP_ADMIN_TOKEN`` and is presented in the
    ``X-Admin-Token`` header. Fail-closed: when the production profile is
    active (``SOS_PROFILE=production``) and the token is unset, every admin
    call is rejected with 403 rather than silently open.
    """
    expected = os.environ.get("SOS_CP_ADMIN_TOKEN", "")
    profile = os.environ.get("SOS_PROFILE", "dev")
    if not expected:
        if profile == "production":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="SOS_CP_ADMIN_TOKEN is not configured (fail-closed in production)",
            )
        return "dev-admin"  # local/dev profile: no token configured, allow
    presented = request.headers.get("x-admin-token", "")
    if presented != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="invalid or missing X-Admin-Token")
    return presented


def get_branding(request: Request) -> BrandingRegistry:
    return request.app.state.branding


def create_app(store: MetadataStore | None = None,
               branding: BrandingRegistry | None = None) -> FastAPI:
    app = FastAPI(
        title="SOS Control Plane — Tenant Provisioning API",
        version="1.0.0",
        description="WP-01 / EPIC-01. Metadata only — zero citizen PII (PII guard enforced).",
    )
    if store is None:
        from .audit_archive import archive_from_env

        store = MetadataStore(
            operators=build_operators(),
            provision_mode=os.environ.get("CONTROL_PLANE_PROVISION_MODE", "sync"),
            archive=archive_from_env(),
        )
    app.state.store = store
    app.state.branding = branding if branding is not None else BrandingRegistry()
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

    # --- Per-state whitelabel branding (app/branding.py) -------------------
    @app.get("/cp/v1/tenants/{state}/branding", response_model=Branding)
    def get_tenant_branding(state: str,
                            registry: BrandingRegistry = Depends(get_branding)) -> Branding:
        """PUBLIC read — frontends fetch effective branding unauthenticated."""
        record = registry.get(state)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no branding for tenant '{state}'")
        return record

    @app.put("/cp/v1/tenants/{state}/branding", response_model=Branding)
    def put_tenant_branding(state: str, record: Branding,
                            registry: BrandingRegistry = Depends(get_branding),
                            _admin: str = Depends(require_admin),
                            actor_name: str = Depends(actor)) -> Branding:
        """Admin update: runtime override over the GitOps-seeded record.

        Every update is appended to the hash-chained branding audit log and
        published as ``ng.sos.tenant.branding_updated``.
        """
        try:
            registry.update(state, record, actor=f"{actor_name}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return record

    @app.get("/cp/v1/branding", response_model=list[Branding])
    def list_branding(registry: BrandingRegistry = Depends(get_branding)) -> list[Branding]:
        """List effective branding for every registered tenant."""
        return registry.list_all()

    @app.post("/cp/v1/domains/verify")
    def verify_domain(req: DomainVerifyRequest,
                      registry: BrandingRegistry = Depends(get_branding)) -> dict:
        """Gateway host-routing lookup: custom_domain -> tenant_state_id."""
        state_id = registry.verify_domain(req.custom_domain)
        if state_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"custom_domain '{req.custom_domain}' is not registered to any tenant",
            )
        return {"custom_domain": req.custom_domain.strip().lower(), "tenant_state_id": state_id}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "control-plane")
    return app


app = create_app()
