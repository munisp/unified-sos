"""Temporal adapter (production deployment wiring) — documented stub.

ADR-005 mandates Temporal for durable titling workflows. This module shows the
mechanical mapping from the local synchronous runner to a Temporal deployment.
It is intentionally import-guarded: ``temporalio`` is a heavyweight dependency
only installed on the workflow-worker image, not in the API/test image.

Production topology
-------------------

* One **task queue** per state tenant (``cadastral-titling-<state_id>``) so
  Benue's 60–90-day directive and Osun's 45-day commitment run the *same*
  workflow class with different SLA parameters injected via
  ``sla_for_state()`` — "one workflow class, different SLA parameters".
* **Workflow**: ``CadastralTitlingWorkflow`` below replays the same transition
  rules as :class:`lands_app.titling.TitlingWorkflow`; approval decisions arrive as
  Temporal **signals** (``approve`` / ``reject``) instead of ``advance()``
  calls, and SLA clocks become durable **timers** (``workflow.wait_condition``
  with timeout) that raise breach flags instead of the injected test clock.
* **Activities** (``@activity.defn``): the functions in :mod:`lands_app.titling`
  (``act_record_application``, ``act_issue_title``) plus PostGIS persistence —
  each gains heartbeat + retry policy (``RetryPolicy(maximum_attempts=5)``)
  and exactly-once semantics via Temporal's activity IDs.
* **Persistence**: workflow history replaces ``LocalTitlingRunner._instances``;
  parcel rows still live in ``cadastre.parcels`` (PostGIS), updated by the
  issuance activity.

Sketch (pseudocode — requires ``pip install temporalio``)::

    from temporalio import activity, workflow
    from lands_app.titling import act_issue_title, STAGE_ORDER, TitlingStage

    @activity.defn
    async def issue_title_activity(...): ...

    @workflow.defn
    class CadastralTitlingWorkflow:
        def __init__(self):
            self._decisions: list[Decision] = []

        @workflow.signal
        def decide(self, decision: Decision) -> None:
            self._decisions.append(decision)

        @workflow.run
        async def run(self, parcel: ParcelRecord) -> str:
            for stage in STAGE_ORDER[1:-1]:
                sla = sla_for_state(parcel.tenant_state_id)
                await workflow.wait_condition(
                    lambda: bool(self._decisions),
                    timeout=timedelta(days=sla.stage_days.get(stage.value, sla.total_days)),
                )  # TimeoutError -> raise SLA breach event to ng.sos.gis.*
                decision = self._decisions.pop(0)
                if not decision.approved:
                    return "REJECTED"
            await workflow.execute_activity(issue_title_activity, parcel, ...)
            return "ISSUED"
"""

from __future__ import annotations

from .sla import sla_for_state
from .titling import STAGE_ORDER, TitlingStage, TitlingWorkflow

__all__ = ["sla_for_state", "STAGE_ORDER", "TitlingStage", "TitlingWorkflow", "temporal_available"]


def temporal_available() -> bool:
    """True when the temporalio SDK is installed (worker images only)."""

    try:  # pragma: no cover - environment dependent
        import temporalio  # noqa: F401
    except ImportError:
        return False
    return True
