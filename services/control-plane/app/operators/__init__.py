"""Provisioning operators — live resource provisioning for control-plane tenants.

Each operator provisions one class of tenant resource (K8s namespace, Postgres
schema/RLS, Keycloak realm, S3 bucket, KMS keyring). Operators implement
upsert semantics so a failed provisioning run can be resumed by re-running
``provision``. All live operators are import-guarded and fail closed
(``OperatorUnavailableError``) when their dependency or configuration is
absent; the deterministic default is the local operator set in ``local.py``.
"""

from .base import (
    OPERATOR_ORDER,
    OperatorUnavailableError,
    ProvisionError,
    ProvisionOperator,
    require_env,
)
from .local import local_operators

__all__ = [
    "OPERATOR_ORDER",
    "OperatorUnavailableError",
    "ProvisionError",
    "ProvisionOperator",
    "local_operators",
    "require_env",
]
