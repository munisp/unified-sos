"""Local operators — deterministic no-op default reproducing the reference
synchronous provisioning steps (no external dependencies, no credentials).

These are the default for development and CI: they record the same workflow
markers as the original fake workflow while going through the real
operator/orchestration path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant


class LocalOperator:
    """No-op operator with upsert semantics (deterministic default)."""

    def __init__(self, name: str, step: str) -> None:
        self.name = name
        self._step = step

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str:
        return self._step

    def decommission(self, tenant_id: str) -> None:  # no-op: nothing to tear down
        return None


def local_operators() -> dict[str, LocalOperator]:
    """Ordered local operator set matching the fixed provisioning order."""
    return {
        "namespace": LocalOperator("namespace", "namespace-created"),
        "postgres": LocalOperator("postgres", "postgres-rls-applied"),
        "keycloak": LocalOperator("keycloak", "keycloak-realm-imported"),
        "s3": LocalOperator("s3", "storage-provisioned"),
        "kms": LocalOperator("kms", "kms-keyring-issued"),
    }
