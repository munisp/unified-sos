"""Domain models and in-memory metadata store for the control plane.

Data boundary (docs/architecture/01-architecture-blueprint.md): the control
plane contains ZERO citizen PII or financial balances — metadata, tenant
configs, and deployment manifests only. The PII guard in ``pii_guard.py``
enforces this at the request boundary.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

# Shared canonical-JSON/SHA-256 hash-chain helpers (services/_shared).
_SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))
from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover
    from .audit_archive import AuditArchive

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
    """Append-only audit event. Sequence numbers are per-process monotonic.

    Each event is hash-chained (SHA-256 over canonical JSON, see
    ``services/_shared/hashchain.py``): ``prev_hash`` links to the previous
    event in the tenant's chain (``GENESIS_PREV_HASH`` for the tenant's
    genesis event) and ``event_hash`` commits to the payload + link, making
    archived copies tamper-evident.
    """

    seq: int
    event_id: str
    event_type: str
    tenant_id: str | None
    actor: str
    detail: dict[str, Any]
    at: str
    prev_hash: str = GENESIS_PREV_HASH
    event_hash: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MetadataStore:
    """Thread-safe in-memory metadata store.

    Production mapping: Postgres metadata schema + OpenSearch immutable audit
    archive (7-year retention). Tenants are keyed by tenant_id; the store
    exposes no delete operations — records are terminal-state only.
    """

    def __init__(self, operators: dict | None = None, provision_mode: str = "sync",
                 archive: "AuditArchive | None" = None) -> None:
        self._lock = threading.Lock()
        self._tenants: dict[str, Tenant] = {}
        self._tenant_by_state: dict[str, str] = {}
        self._policy_packs: dict[str, list[PolicyPackRecord]] = {}
        self._audit: list[AuditEvent] = []
        self._seq = 0
        #: Last event_hash per tenant chain (key: tenant_id or "_global").
        self._last_hash: dict[str, str] = {}
        #: Optional immutable archive sink (LocalFileArchive/OpenSearchArchive);
        #: every appended event is mirrored there write-only.
        self._archive = archive
        from .operators import local_operators

        #: Provisioning operators (default: deterministic local no-op set) and
        #: mode (sync | async) per CONTROL_PLANE_PROVISION_MODE.
        self._operators = operators if operators is not None else local_operators()
        if provision_mode not in ("sync", "async"):
            raise ValueError(f"invalid provision_mode '{provision_mode}' (sync|async)")
        self._provision_mode = provision_mode
        self._worker = None
        if provision_mode == "async":
            from .provisioning import ProvisioningWorker

            self._worker = ProvisioningWorker(self)

    # --- tenants ---------------------------------------------------------
    def create_tenant(self, req: TenantCreate, actor: str = "control-plane") -> Tenant:
        """Provision a tenant: provisioning -> active workflow, recorded stepwise.

        Idempotent on (state): re-posting an existing active/suspended tenant
        returns the existing record; re-posting a FAILED tenant retries the
        provisioning workflow (operator upsert semantics make resume safe).
        In async mode the tenant is returned in ``provisioning`` state and an
        in-process worker finalizes it in the background (202 + poll).
        """
        with self._lock:
            existing_id = self._tenant_by_state.get(req.state)
            if existing_id is not None:
                existing = self._tenants[existing_id]
                if existing.status != TenantStatus.FAILED:
                    return existing
                # Retry of a failed provisioning run (idempotent resume).
                if self._provision_mode == "async":
                    existing.status = TenantStatus.PROVISIONING
                    existing.updated_at = _now()
                    existing.workflow.append(f"{existing.updated_at} retry-requested")
                    assert self._worker is not None
                    self._worker.enqueue(existing.tenant_id)
                    return existing
                return self._provision_locked(existing, actor)

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
            self._append_locked("ng.sos.tenant.provisioning_requested", tenant_id, actor,
                                {"state": req.state, "tier": req.tier})

            if self._provision_mode == "async":
                assert self._worker is not None
                self._worker.enqueue(tenant_id)
                return tenant
            return self._provision_locked(tenant, actor)

    def _provision_locked(self, tenant: Tenant, actor: str) -> Tenant:
        """Run the orchestrated provisioning workflow (caller holds the lock)."""
        from .provisioning import ProvisioningWorkflowError, run_provisioning_workflow

        def emit(event_type: str, tid: str, detail: dict) -> None:
            self._append_locked(event_type, tid, actor, detail)

        try:
            run_provisioning_workflow(tenant, self._operators, emit=emit)
        except ProvisioningWorkflowError as exc:
            self._append_locked("ng.sos.tenant.provision_failed", tenant.tenant_id, actor,
                                {"state": tenant.state, "error": str(exc)})
            return tenant
        self._append_locked("ng.sos.tenant.provisioned", tenant.tenant_id, actor,
                            {"state": tenant.state, "tier": tenant.tier})
        return tenant

    def complete_provisioning(self, tenant_id: str,
                              actor: str = "control-plane-worker") -> Tenant | None:
        """Finalize an asynchronously enqueued tenant (in-process worker)."""
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None or tenant.status != TenantStatus.PROVISIONING:
                return tenant
            return self._provision_locked(tenant, actor)

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
    @staticmethod
    def _chain_key(tenant_id: str | None) -> str:
        return tenant_id if tenant_id is not None else "_global"

    def _append_locked(self, event_type: str, tenant_id: str | None, actor: str,
                       detail: dict[str, Any]) -> None:
        # Genesis event anchors each tenant's hash chain exactly once.
        if self._chain_key(tenant_id) not in self._last_hash:
            self._append_chained_locked(
                "ng.sos.audit.genesis", tenant_id, "control-plane",
                {"tenant_id": tenant_id, "chain": "sha256/canonical-json"})
        self._append_chained_locked(event_type, tenant_id, actor, detail)

    def _append_chained_locked(self, event_type: str, tenant_id: str | None, actor: str,
                               detail: dict[str, Any]) -> None:
        key = self._chain_key(tenant_id)
        prev_hash = self._last_hash.get(key, GENESIS_PREV_HASH)
        self._seq += 1
        event = AuditEvent(
            seq=self._seq, event_id=f"evt-{self._seq:06d}", event_type=event_type,
            tenant_id=tenant_id, actor=actor, detail=detail, at=_now(),
            prev_hash=prev_hash,
        )
        event.event_hash = event_payload_hash(
            event.model_dump(exclude={"prev_hash", "event_hash"}), prev_hash)
        self._last_hash[key] = event.event_hash
        self._audit.append(event)
        if self._archive is not None:
            self._archive.append(event)

    def audit_events(self) -> list[AuditEvent]:
        """Read-only view of the append-only audit log (no mutation API exists)."""
        return list(self._audit)
