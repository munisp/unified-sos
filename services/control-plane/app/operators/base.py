"""Operator base types — fail-closed provisioning contract."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant

#: Fixed provisioning order. Rollback runs in the reverse of this order.
OPERATOR_ORDER: tuple[str, ...] = ("namespace", "postgres", "keycloak", "s3", "kms")


class OperatorUnavailableError(RuntimeError):
    """Raised when a live operator's dependency or configuration is missing.

    Live mode is fail-closed: the control plane must refuse to boot rather
    than silently degrade to fake provisioning.
    """


class ProvisionError(RuntimeError):
    """Raised when an operator fails mid-provisioning (triggers rollback)."""


class ProvisionOperator(Protocol):
    """One step of the tenant provisioning workflow.

    ``provision`` is idempotent (upsert semantics): re-running it for the
    same tenant must converge to the same end state, so a workflow retry
    after a partial failure can resume safely. It returns the workflow step
    marker recorded on the tenant (e.g. ``"namespace-created"``).
    """

    name: str

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str: ...

    def decommission(self, tenant_id: str) -> None:
        """Best-effort compensating teardown used by rollback."""
        ...


def require_env(*names: str) -> dict[str, str]:
    """Return the values of required env vars, fail closed listing missing.

    Raises:
        OperatorUnavailableError: listing exactly which variables are missing.
    """
    values = {name: os.environ.get(name, "") for name in names}
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise OperatorUnavailableError(
            "missing required configuration: " + ", ".join(missing)
        )
    return values
