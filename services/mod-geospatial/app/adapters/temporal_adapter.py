"""Temporal adapter (production deployment wiring) — import-guarded.

ADR-005 mandates Temporal for durable orchestration. This module maps the
deterministic local geospatial runners (``geospatial/local/*``) onto Temporal
workflows/activities:

* One **task queue per state tenant**: ``geospatial-jobs-<state_id>`` so each
  state's jobs run on its own worker pool ("one workflow class, per-tenant
  queues").
* **Workflows** wrap the local deterministic functions as **activities** with
  ``RetryPolicy(maximum_attempts=5)``; workflow history replaces in-process
  job bookkeeping in production.
* Import-guarded: ``temporalio`` is only installed on the workflow-worker
  image, not in the API/test image. ``temporal_available()`` reports whether
  the SDK is present; constructing a client/worker without it fails closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from ..domain import AdapterUnavailableError

TEMPORAL_ADDRESS_ENV = "GEOSPATIAL_TEMPORAL_ADDRESS"
TEMPORAL_NAMESPACE_ENV = "GEOSPATIAL_TEMPORAL_NAMESPACE"

#: Activity retry policy: at most 5 attempts per ADR-005 wiring conventions.
ACTIVITY_RETRY_ATTEMPTS = 5
ACTIVITY_START_TO_CLOSE = timedelta(minutes=30)


def temporal_available() -> bool:
    """True when the temporalio SDK is installed (worker images only)."""

    try:  # pragma: no cover - environment dependent
        import temporalio  # noqa: F401
    except ImportError:
        return False
    return True


def task_queue_for_state(state_id: str) -> str:
    """Per-tenant task queue: ``geospatial-jobs-<state_id>``."""

    state = "".join(c if c.isalnum() or c == "-" else "-" for c in state_id.strip().lower())
    if not state:
        raise ValueError("state_id must be non-empty")
    return f"geospatial-jobs-{state}"


@dataclass
class GeospatialJobInput:
    """Workflow input: a queued geospatial processing job."""

    job_id: str
    tenant_state_id: str
    job_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeospatialJobOutput:
    """Workflow output: metrics + optional artifact URI."""

    job_id: str
    result_type: str
    metrics: dict[str, Any]
    result_uri: Optional[str] = None


# ---------------------------------------------------------------------------
# Activities — thin wrappers over the deterministic local runners. Defined
# unconditionally so they are importable without the SDK (plain async
# functions); the ``@activity.defn`` decorators are applied when temporalio
# is installed.
# ---------------------------------------------------------------------------


async def run_unassessed_property_join(input: GeospatialJobInput) -> GeospatialJobOutput:
    """Activity: run the deterministic local unassessed-property join."""

    from ..service import GeospatialService
    from ..repository import InMemoryGeospatialRepository
    from ..domain import JobType

    service = GeospatialService(InMemoryGeospatialRepository())
    job = service.create_job(
        input.tenant_state_id, {"job_type": JobType.UNASSESSED_PROPERTY_JOIN.value, "parameters": input.parameters}
    )
    job = service.run_job(input.tenant_state_id, job.job_id)
    result = service.repo.list_results(input.tenant_state_id, job.job_id)[-1]
    return GeospatialJobOutput(
        job_id=input.job_id,
        result_type=result.result_type,
        metrics=result.metrics,
        result_uri=result.result_uri,
    )


async def run_ndvi_change_detection(input: GeospatialJobInput) -> GeospatialJobOutput:
    """Activity: run the deterministic local NDVI change detection."""

    from ..service import GeospatialService
    from ..repository import InMemoryGeospatialRepository
    from ..domain import JobType

    service = GeospatialService(InMemoryGeospatialRepository())
    job = service.create_job(
        input.tenant_state_id, {"job_type": JobType.NDVI_CHANGE_DETECTION.value, "parameters": input.parameters}
    )
    job = service.run_job(input.tenant_state_id, job.job_id)
    result = service.repo.list_results(input.tenant_state_id, job.job_id)[-1]
    return GeospatialJobOutput(
        job_id=input.job_id,
        result_type=result.result_type,
        metrics=result.metrics,
        result_uri=result.result_uri,
    )


if temporal_available():  # pragma: no cover - requires temporalio
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy

    run_unassessed_property_join = activity.defn(run_unassessed_property_join)
    run_ndvi_change_detection = activity.defn(run_ndvi_change_detection)

    _RETRY = RetryPolicy(maximum_attempts=ACTIVITY_RETRY_ATTEMPTS)

    @workflow.defn
    class GeospatialJobWorkflow:
        """Durable geospatial job: executes the matching local runner as an
        activity with RetryPolicy(5) on the per-tenant task queue."""

        @workflow.run
        async def run(self, input: GeospatialJobInput) -> GeospatialJobOutput:
            activity_fn = ACTIVITY_BY_JOB_TYPE.get(input.job_type)
            if activity_fn is None:
                raise ValueError(f"unsupported geospatial job type {input.job_type!r}")
            return await workflow.execute_activity(
                activity_fn,
                input,
                start_to_close_timeout=ACTIVITY_START_TO_CLOSE,
                retry_policy=_RETRY,
            )

else:
    GeospatialJobWorkflow = None  # type: ignore[assignment]


#: Job type → activity function (decorated when temporalio is installed).
ACTIVITY_BY_JOB_TYPE = {
    "UNASSESSED_PROPERTY_JOIN": run_unassessed_property_join,
    "NDVI_CHANGE_DETECTION": run_ndvi_change_detection,
}


async def start_geospatial_job(input: GeospatialJobInput) -> str:
    """Start ``GeospatialJobWorkflow`` on the per-tenant task queue.

    Fails closed when the temporalio SDK is not installed or no server
    address is configured.
    """

    if not temporal_available():
        raise AdapterUnavailableError(
            "temporalio SDK is not installed; Temporal adapter fails closed"
        )
    address = os.environ.get(TEMPORAL_ADDRESS_ENV)
    if not address:
        raise AdapterUnavailableError(
            f"{TEMPORAL_ADDRESS_ENV} is not set; Temporal adapter fails closed"
        )
    from temporalio.client import Client  # pragma: no cover - production path

    client = await Client.connect(  # pragma: no cover - production path
        address, namespace=os.environ.get(TEMPORAL_NAMESPACE_ENV, "default")
    )
    handle = await client.start_workflow(  # pragma: no cover - production path
        "GeospatialJobWorkflow",
        input,
        id=f"geospatial-job-{input.job_id}",
        task_queue=task_queue_for_state(input.tenant_state_id),
    )
    return handle.id
