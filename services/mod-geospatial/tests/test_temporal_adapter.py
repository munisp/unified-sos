"""Tests for the Temporal adapter seam (import-guarded; replay skip-gated)."""

from __future__ import annotations

import asyncio

import pytest

from app.adapters import temporal_adapter
from app.adapters.temporal_adapter import (
    GeospatialJobInput,
    task_queue_for_state,
    temporal_available,
)
from app.domain import AdapterUnavailableError


def test_task_queue_per_state():
    assert task_queue_for_state("osun") == "geospatial-jobs-osun"
    assert task_queue_for_state("Lagos") == "geospatial-jobs-lagos"
    assert task_queue_for_state("nasarawa ") == "geospatial-jobs-nasarawa"


def test_task_queue_sanitizes_and_rejects_empty():
    assert task_queue_for_state("Ogun_State") == "geospatial-jobs-ogun-state"
    with pytest.raises(ValueError):
        task_queue_for_state("   ")


def test_start_fails_closed_without_sdk_or_address(monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_TEMPORAL_ADDRESS", raising=False)
    job = GeospatialJobInput(job_id="j1", tenant_state_id="osun", job_type="H3_AGGREGATION")
    if temporal_available():
        # SDK present but no server address: still fails closed.
        with pytest.raises(AdapterUnavailableError, match="GEOSPATIAL_TEMPORAL_ADDRESS"):
            asyncio.run(temporal_adapter.start_geospatial_job(job))
    else:
        with pytest.raises(AdapterUnavailableError, match="temporalio"):
            asyncio.run(temporal_adapter.start_geospatial_job(job))
        assert temporal_adapter.GeospatialJobWorkflow is None


@pytest.mark.skipif(not temporal_available(), reason="temporalio SDK not installed (worker image only)")
def test_geospatial_job_workflow_replay():
    """Replay/determinism test for GeospatialJobWorkflow.

    Runs the workflow in Temporal's time-skipping test environment with the
    deterministic local NDVI runner as the activity; asserts the workflow
    completes with the local backend marker. Re-running the same history is
    deterministic because activities are the only non-deterministic boundary.
    """

    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    async def _run():
        params = {
            "b04_t0": [[0.1, 0.1], [0.1, 0.1]],
            "b08_t0": [[0.5, 0.5], [0.5, 0.5]],
            "b04_t1": [[0.3, 0.1], [0.1, 0.1]],
            "b08_t1": [[0.4, 0.5], [0.5, 0.5]],
            "scene_id": "replay-scene",
        }
        job = GeospatialJobInput(
            job_id="wf-replay-1",
            tenant_state_id="osun",
            job_type="NDVI_CHANGE_DETECTION",
            parameters=params,
        )
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue=task_queue_for_state("osun"),
                workflows=[temporal_adapter.GeospatialJobWorkflow],
                activities=[temporal_adapter.run_ndvi_change_detection],
            ):
                return await env.client.execute_workflow(
                    temporal_adapter.GeospatialJobWorkflow.run,
                    job,
                    id="wf-replay-1",
                    task_queue=task_queue_for_state("osun"),
                )

    result = asyncio.run(_run())
    assert result.job_id == "wf-replay-1"
    assert result.metrics["backend"] == "geospatial.local"
