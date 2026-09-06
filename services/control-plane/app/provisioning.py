"""Tenant provisioning orchestration.

``run_provisioning_workflow`` drives the fixed operator order
namespace → postgres → keycloak → s3 → kms with per-step audit events and
compensating rollback in reverse order on failure (tenant ends FAILED).
Operators implement upsert semantics so a retry re-runs the full workflow
and converges.

``ProvisioningWorker`` is the in-process async worker used when
``CONTROL_PLANE_PROVISION_MODE=async``: create returns 202/``provisioning``
immediately and the worker finalizes the tenant in the background.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Callable, Mapping

from .operators.base import OPERATOR_ORDER, ProvisionOperator
from .pii_guard import sanitize_error_message

if TYPE_CHECKING:  # pragma: no cover
    from .domain import Tenant

log = logging.getLogger("control-plane.provisioning")

#: Audit-event sink: (event_type, tenant_id, detail) -> None
AuditSink = Callable[[str, str, dict], None]

_STEP_AUDIT = "ng.sos.tenant.provision_step"
_ROLLBACK_AUDIT = "ng.sos.tenant.provision_rollback"


class ProvisioningWorkflowError(RuntimeError):
    """Raised after rollback when a provisioning step failed."""


def _now_workflow(tenant: "Tenant", marker: str) -> None:
    from .domain import _now

    tenant.workflow.append(f"{_now()} {marker}")
    tenant.updated_at = _now()


def run_provisioning_workflow(
    tenant: "Tenant",
    operators: Mapping[str, ProvisionOperator],
    emit: AuditSink | None = None,
) -> "Tenant":
    """Run the fixed provisioning workflow for ``tenant``.

    On step failure: compensating rollback runs in reverse order over the
    operators that already completed, the tenant is marked FAILED, and a
    ``ProvisioningWorkflowError`` is raised. Operator error text is sanitized
    (PII guard) before being recorded in the workflow/audit trail.
    """

    def audit(event_type: str, detail: dict) -> None:
        if emit is not None:
            emit(event_type, tenant.tenant_id, detail)

    completed: list[ProvisionOperator] = []
    for name in OPERATOR_ORDER:
        operator = operators[name]
        audit(_STEP_AUDIT, {"step": name, "operator": operator.name, "status": "started"})
        try:
            marker = operator.provision(tenant, tenant.provisioned_resources)
        except Exception as exc:
            safe = sanitize_error_message(str(exc))
            audit(_STEP_AUDIT, {"step": name, "operator": operator.name,
                                "status": "failed", "error": safe})
            _now_workflow(tenant, f"failed:{name}: {safe}")
            _rollback(tenant, completed, audit)
            from .domain import TenantStatus

            tenant.status = TenantStatus.FAILED
            _now_workflow(tenant, "failed")
            raise ProvisioningWorkflowError(
                f"provisioning failed at step '{name}': {safe}"
            ) from exc
        _now_workflow(tenant, marker)
        audit(_STEP_AUDIT, {"step": name, "operator": operator.name,
                            "status": "completed"})
        completed.append(operator)

    from .domain import TenantStatus

    tenant.status = TenantStatus.ACTIVE
    _now_workflow(tenant, "active")
    return tenant


def _rollback(tenant: "Tenant", completed: list[ProvisionOperator],
              audit: Callable[[str, dict], None]) -> None:
    """Compensating teardown of completed steps, in reverse order."""
    for operator in reversed(completed):
        try:
            operator.decommission(tenant.tenant_id)
            audit(_ROLLBACK_AUDIT, {"operator": operator.name, "status": "rolled-back"})
        except Exception as exc:  # rollback is best-effort; keep unwinding
            safe = sanitize_error_message(str(exc))
            audit(_ROLLBACK_AUDIT, {"operator": operator.name,
                                    "status": "rollback-failed", "error": safe})
            log.error("rollback failed for operator %s: %s", operator.name, safe)


class ProvisioningWorker:
    """In-process background worker for async provisioning mode.

    Production mapping: Argo Workflows / a durable task queue; this worker
    keeps the reference implementation dependency-free while preserving the
    202 + poll semantics of the contract.
    """

    def __init__(self, store: "object") -> None:
        from .domain import MetadataStore

        assert isinstance(store, MetadataStore)
        self._store = store
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="cp-provisioning-worker", daemon=True
        )
        self._thread.start()

    def enqueue(self, tenant_id: str) -> None:
        self._queue.put(tenant_id)

    def _run(self) -> None:
        while True:
            tenant_id = self._queue.get()
            if tenant_id is None:  # shutdown sentinel
                self._queue.task_done()
                return
            try:
                self._store.complete_provisioning(tenant_id)
            except Exception:  # pragma: no cover - defensive
                log.exception("async provisioning crashed for %s", tenant_id)
            finally:
                self._queue.task_done()

    def join(self, timeout: float | None = None) -> None:
        """Test hook: block until the queue drains."""
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("provisioning worker did not drain in time")
            time.sleep(0.01)

    def close(self) -> None:
        self._queue.put(None)
