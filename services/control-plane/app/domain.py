"""Domain models and in-memory metadata store for the control plane.

Data boundary (docs/architecture/01-architecture-blueprint.md): the control
plane contains ZERO citizen PII or financial balances — metadata, tenant
configs, and deployment manifests only. The PII guard in ``pii_guard.py``
enforces this at the request boundary.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

#: Tenant states + tiers per contracts/openapi/control-plane.yaml.
VALID_STATES = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")
VALID_TIERS = ("dedicated", "hybrid", "shared")


class TenantStatus(str, Enum):
    """Tenant lifecycle workflow states."""

    PROVISIONING = "provisioning"
    ACTIVE = "active"
    FAILED = "failed"
    SUSPENDED = "suspended"


class TenantCreate(BaseModel):
    """CreateTenant request body (contract schema)."""

    state: str = Field(..., pattern="^(lagos|ogun|osun|benue|nasarawa|taraba)$")
    tier: str = Field(..., pattern="^(dedicated|hybrid|shared)$")
    realms: list[str] = Field(default_factory=list)


class ProvisionedResources(BaseModel):
    k8s_namespace: str
    postgres_schema: str
    keycloak_realm: str
    s3_bucket: str
    kms_keyring: str


class Tenant(BaseModel):
    tenant_id: str
    state: str
    tier: str
    status: TenantStatus
    realms: list[str]
    provisioned_resources: ProvisionedResources
    workflow: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class TenantOperation(BaseModel):
    """Async provisioning handle (contract schema, 202 response)."""

    tenant_id: str
    status: TenantStatus
    provisioned_resources: ProvisionedResources


class PolicyPackRef(BaseModel):
    policy_id: str
    document: dict[str, Any]


class PolicyPackRecord(BaseModel):
    policy_id: str
    tenant_id: str
    state: str
    activated_at: str
    document: dict[str, Any]


class AuditEvent(BaseModel):
    """Append-only audit event. Sequence numbers are per-process monotonic."""

    seq: int
    event_id: str
    event_type: str
    tenant_id: str | None
    actor: str
    detail: dict[str, Any]
    at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MetadataStore:
    """Thread-safe in-memory metadata store.

    Production mapping: Postgres metadata schema + OpenSearch immutable audit
    archive (7-year retention). Tenants are keyed by tenant_id; the store
    exposes no delete operations — records are terminal-state only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenants: dict[str, Tenant] = {}
        self._tenant_by_state: dict[str, str] = {}
        self._policy_packs: dict[str, list[PolicyPackRecord]] = {}
        self._audit: list[AuditEvent] = []
        self._seq = 0

    # --- tenants ---------------------------------------------------------
    def create_tenant(self, req: TenantCreate, actor: str = "control-plane") -> Tenant:
        """Provision a tenant: provisioning -> active workflow, recorded stepwise.

        Idempotent on (state): re-posting an existing active/suspended tenant
        returns the existing record.
        """
        with self._lock:
            existing_id = self._tenant_by_state.get(req.state)
            if existing_id is not None:
                return self._tenants[existing_id]

            tenant_id = f"tn-{req.state}-{uuid4().hex[:8]}"
            resources = ProvisionedResources(
                k8s_namespace=f"sos-{req.state}-{req.tier}",
                postgres_schema=f"tenant_{req.state}",
                keycloak_realm=f"sos-{req.state}",
                s3_bucket=f"sos-{req.state}-{req.tier}-data",
                kms_keyring=f"sos-{req.state}-keyring",
            )
            now = _now()
            tenant = Tenant(
                tenant_id=tenant_id,
                state=req.state,
                tier=req.tier,
                status=TenantStatus.PROVISIONING,
                realms=req.realms or [f"sos-{req.state}"],
                provisioned_resources=resources,
                workflow=[f"{now} requested"],
                created_at=now,
                updated_at=now,
            )
            self._tenants[tenant_id] = tenant
            self._tenant_by_state[req.state] = tenant_id

            # Workflow: reference implementation completes synchronously (< 90 s
            # acceptance is a property of the production K8s/ArgoCD pipeline).
            for step in ("namespace-created", "postgres-rls-applied", "keycloak-realm-imported",
                         "storage-provisioned", "kms-keyring-issued"):
                tenant.workflow.append(f"{_now()} {step}")
            tenant.status = TenantStatus.ACTIVE
            tenant.updated_at = _now()
            tenant.workflow.append(f"{tenant.updated_at} active")
            self._append_locked("ng.sos.tenant.provisioned", tenant_id, actor,
                                {"state": req.state, "tier": req.tier})
            return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[Tenant]:
        return [self._tenants[k] for k in sorted(self._tenants)]

    def suspend_tenant(self, tenant_id: str, reason: str, actor: str) -> Tenant | None:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None:
                return None
            if tenant.status != TenantStatus.SUSPENDED:
                tenant.status = TenantStatus.SUSPENDED
                tenant.updated_at = _now()
                tenant.workflow.append(f"{tenant.updated_at} suspended: {reason}")
                self._append_locked("ng.sos.tenant.suspended", tenant_id, actor, {"reason": reason})
            return tenant

    # --- policy packs ------------------------------------------------------
    def activate_policy_pack(self, tenant_id: str, ref: PolicyPackRef, actor: str) -> PolicyPackRecord | None:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None:
                return None
            record = PolicyPackRecord(
                policy_id=ref.policy_id,
                tenant_id=tenant_id,
                state=tenant.state,
                activated_at=_now(),
                document=ref.document,
            )
            self._policy_packs.setdefault(tenant_id, []).append(record)
            self._append_locked("ng.sos.policy_pack.activated", tenant_id, actor,
                                {"policy_id": ref.policy_id})
            return record

    def list_policy_packs(self, tenant_id: str) -> list[PolicyPackRecord]:
        return list(self._policy_packs.get(tenant_id, []))

    # --- audit -------------------------------------------------------------
    def _append_locked(self, event_type: str, tenant_id: str | None, actor: str,
                       detail: dict[str, Any]) -> None:
        self._seq += 1
        self._audit.append(AuditEvent(
            seq=self._seq, event_id=f"evt-{self._seq:06d}", event_type=event_type,
            tenant_id=tenant_id, actor=actor, detail=detail, at=_now(),
        ))

    def audit_events(self) -> list[AuditEvent]:
        """Read-only view of the append-only audit log (no mutation API exists)."""
        return list(self._audit)
