"""CadastralTitlingWorkflow — multi-stage e-C-of-O approval state machine.

Temporal-ready design (ADR-005): the workflow is *code-defined* with a clean
activity/workflow separation —

* **Activities** (:mod:`activities` functions below) are side-effecting units
  (registry updates, signing, notifications) that Temporal would wrap with
  ``@activity.defn`` and retry/timeout policies.
* **Workflow** (:class:`TitlingWorkflow`) is pure orchestration over instance
  state: stage transition rules, approval signals, SLA instrumentation. It
  never touches storage directly — all persistence goes through injected
  repositories, which is exactly the seam Temporal activities use.

``LocalTitlingRunner`` executes the same workflow synchronously and
in-memory for tests/CI. Production swaps it for the Temporal adapter in
:mod:`lands_app.temporal_adapter` (same activities, durable history, real timers).

Stage topology (README + ADR-005):
  APPLICATION_RECEIVED → SURVEYOR_VALIDATION (TAGIS/NAGIS/etc. clearance)
  → MINISTRY_REVIEW (Town Planning) → ATTORNEY_GENERAL_REVIEW
  → GOVERNOR_CONSENT (digital signature) → ISSUANCE (signed e-C-of-O).
Any stage may REJECT, ending the workflow.
"""

from __future__ import annotations

import enum
import hashlib
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .schemas import ParcelRecord, ParcelStatus, TitleType
from . import signing
from .sla import SLAConfig, SLAEvaluation, evaluate_sla, sla_for_state


class TitlingStage(str, enum.Enum):
    APPLICATION_RECEIVED = "APPLICATION_RECEIVED"
    SURVEYOR_VALIDATION = "SURVEYOR_VALIDATION"
    MINISTRY_REVIEW = "MINISTRY_REVIEW"
    ATTORNEY_GENERAL_REVIEW = "ATTORNEY_GENERAL_REVIEW"
    GOVERNOR_CONSENT = "GOVERNOR_CONSENT"
    ISSUANCE = "ISSUANCE"


#: Linear happy-path order. Each stage must be approved to unlock the next.
STAGE_ORDER: tuple[TitlingStage, ...] = (
    TitlingStage.APPLICATION_RECEIVED,
    TitlingStage.SURVEYOR_VALIDATION,
    TitlingStage.MINISTRY_REVIEW,
    TitlingStage.ATTORNEY_GENERAL_REVIEW,
    TitlingStage.GOVERNOR_CONSENT,
    TitlingStage.ISSUANCE,
)


class WorkflowStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"


class WorkflowError(Exception):
    """Illegal transition or decision on a workflow instance."""


@dataclass(frozen=True)
class StageTransition:
    """One entry in the durable workflow history (Temporal event history equiv.)."""

    stage: TitlingStage
    actor: str
    approved: bool
    entered_at: datetime
    exited_at: datetime
    note: str = ""


@dataclass
class TitlingWorkflowInstance:
    """State of one e-C-of-O application (Temporal workflow execution equiv.)."""

    workflow_id: str
    tenant_state_id: str
    parcel_id: UUID
    stage: TitlingStage
    status: WorkflowStatus
    started_at: datetime
    history: list[StageTransition] = field(default_factory=list)
    c_of_o_number: Optional[str] = None
    signed_title_jws: Optional[str] = None
    governor_consent_jws: Optional[str] = None
    rejection_reason: Optional[str] = None

    def sla_evaluation(self, now: Optional[datetime] = None) -> SLAEvaluation:
        """Evaluate the tenant's SLA clocks against the recorded history."""

        config = sla_for_state(self.tenant_state_id)
        intervals: dict[str, tuple[datetime, Optional[datetime]]] = {}
        # Reconstruct (entered, exited) intervals per approval stage from history.
        for i, t in enumerate(self.history):
            if t.stage == TitlingStage.APPLICATION_RECEIVED:
                continue
            intervals[t.stage.value] = (t.entered_at, t.exited_at)
        # Current open stage is clocked from the last transition to `now`.
        if self.status == WorkflowStatus.RUNNING and self.history:
            last = self.history[-1]
            next_stage = next_pending_stage(self)
            if next_stage is not None:
                intervals.setdefault(next_stage.value, (last.exited_at, None))
        return evaluate_sla(config, self.started_at, intervals, now)


def next_pending_stage(instance: TitlingWorkflowInstance) -> Optional[TitlingStage]:
    """The stage awaiting a decision, or None if the workflow is terminal."""

    if instance.status != WorkflowStatus.RUNNING:
        return None
    completed = [t.stage for t in instance.history]
    if len(completed) >= len(STAGE_ORDER) - 1:
        return None
    return STAGE_ORDER[len(completed)]


# ---------------------------------------------------------------------------
# Activities (Temporal @activity.defn equivalents — side effects live here)
# ---------------------------------------------------------------------------


def act_record_application(instance: TitlingWorkflowInstance, actor: str, now: datetime) -> StageTransition:
    """Activity: register that the e-C-of-O application was received."""
    return StageTransition(
        stage=TitlingStage.APPLICATION_RECEIVED,
        actor=actor,
        approved=True,
        entered_at=now,
        exited_at=now,
        note="Application received and parcel registered",
    )


def act_issue_title(
    instance: TitlingWorkflowInstance,
    parcel: ParcelRecord,
    registry_key: Ed25519PrivateKey,
    governor_key: Ed25519PrivateKey,
    c_of_o_number: str,
    now: datetime,
) -> tuple[str, str]:
    """Activity: mint the cryptographically signed e-C-of-O title document.

    Returns (title_jws, governor_consent_jws). The consent token binds the
    governor's digital signature to a hash of the title token, forming the
    two-link signature chain returned by the deeds/verify endpoint.
    """
    payload = signing.build_title_payload(
        tenant_state_id=instance.tenant_state_id,
        parcel_id=str(instance.parcel_id),
        parcel_uin=parcel.parcel_uin,
        owner_stin=parcel.owner_stin,
        c_of_o_number=c_of_o_number,
        area_sqm=parcel.area_sqm,
        survey_plan_no=parcel.survey_plan_no,
        issued_at=now.isoformat(),
        workflow_id=instance.workflow_id,
    )
    title_jws = signing.sign_payload(payload, registry_key)
    consent_payload = {
        "doc_type": "GOVERNOR_CONSENT",
        "tenant_state_id": instance.tenant_state_id,
        "title_sha256": hashlib.sha256(title_jws.encode("ascii")).hexdigest(),
        "consented_at": now.isoformat(),
        "titling_workflow_id": instance.workflow_id,
    }
    consent_jws = signing.sign_payload(consent_payload, governor_key)
    return title_jws, consent_jws


# ---------------------------------------------------------------------------
# Workflow orchestration (pure transitions — no I/O)
# ---------------------------------------------------------------------------


class TitlingWorkflow:
    """CadastralTitlingWorkflow: stage transition rules + SLA instrumentation."""

    def __init__(
        self,
        registry_key: Ed25519PrivateKey,
        governor_key: Ed25519PrivateKey,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sequence: Callable[[], int] | None = None,
    ) -> None:
        self._registry_key = registry_key
        self._governor_key = governor_key
        self._clock = clock
        self._seq = sequence or itertools.count(1).__next__

    # -- workflow entry point ------------------------------------------------
    def start(self, parcel: ParcelRecord, actor: str = "lands-registry") -> TitlingWorkflowInstance:
        """Start a workflow for a freshly registered parcel (APPLICATION_RECEIVED)."""
        now = self._clock()
        instance = TitlingWorkflowInstance(
            workflow_id=f"cadastral-titling-{parcel.parcel_uin}-{uuid4().hex[:8]}",
            tenant_state_id=parcel.tenant_state_id,
            parcel_id=parcel.parcel_id,
            stage=TitlingStage.APPLICATION_RECEIVED,
            status=WorkflowStatus.RUNNING,
            started_at=now,
        )
        instance.history.append(act_record_application(instance, actor, now))
        return instance

    # -- signal handler (Temporal workflow signal equivalent) -----------------
    def advance(
        self,
        instance: TitlingWorkflowInstance,
        parcel: ParcelRecord,
        *,
        approved: bool,
        actor: str,
        note: str = "",
    ) -> TitlingWorkflowInstance:
        """Apply an approval/rejection decision to the pending stage.

        On final GOVERNOR_CONSENT approval the issuance activity runs inline
        (Temporal would schedule it as an activity task): title_type flips to
        C_OF_O, a C-of-O number is minted, and the title is Ed25519-signed.
        """
        if instance.status != WorkflowStatus.RUNNING:
            raise WorkflowError(f"workflow {instance.workflow_id} is already {instance.status.value}")

        pending = next_pending_stage(instance)
        if pending in (None, TitlingStage.APPLICATION_RECEIVED, TitlingStage.ISSUANCE):
            raise WorkflowError(f"no approvable stage pending (status={instance.status.value})")

        now = self._clock()
        entered = instance.history[-1].exited_at
        instance.history.append(
            StageTransition(
                stage=pending, actor=actor, approved=approved,
                entered_at=entered, exited_at=now, note=note,
            )
        )

        if not approved:
            instance.status = WorkflowStatus.REJECTED
            instance.rejection_reason = note or f"rejected at {pending.value} by {actor}"
            return instance

        if pending == TitlingStage.GOVERNOR_CONSENT:
            # Issuance activity: number + sign + flip parcel title state.
            year = now.year
            c_of_o = f"{instance.tenant_state_id.upper()}/COFO/{year}/{self._seq():06d}"
            title_jws, consent_jws = act_issue_title(
                instance, parcel, self._registry_key, self._governor_key, c_of_o, now
            )
            instance.c_of_o_number = c_of_o
            instance.signed_title_jws = title_jws
            instance.governor_consent_jws = consent_jws
            instance.stage = TitlingStage.ISSUANCE
            instance.status = WorkflowStatus.ISSUED
            instance.history.append(
                StageTransition(
                    stage=TitlingStage.ISSUANCE, actor="lands-registry",
                    approved=True, entered_at=now, exited_at=now,
                    note=f"e-C-of-O {c_of_o} issued and signed",
                )
            )
        else:
            instance.stage = STAGE_ORDER[STAGE_ORDER.index(pending) + 1]
        return instance


# ---------------------------------------------------------------------------
# Local synchronous runner (tests/CI; Temporal adapter is the production twin)
# ---------------------------------------------------------------------------


class LocalTitlingRunner:
    """Synchronous in-memory workflow runner.

    Mirrors Temporal's client semantics: ``start`` starts an execution,
    ``advance`` delivers an approval signal, ``get`` fetches state, and
    ``run_to_completion`` is a test convenience that approves every stage.
    The injected ``clock`` lets tests simulate the passage of time for SLA
    breach assertions (Temporal would use durable workflow timers).
    """

    def __init__(
        self,
        registry_key: Ed25519PrivateKey | None = None,
        governor_key: Ed25519PrivateKey | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        registry_key = registry_key or signing.generate_keypair()[0]
        governor_key = governor_key or signing.generate_keypair()[0]
        self.registry_public_key: Ed25519PublicKey = registry_key.public_key()
        self.governor_public_key: Ed25519PublicKey = governor_key.public_key()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._workflow = TitlingWorkflow(registry_key, governor_key, self._clock)
        self._instances: dict[str, TitlingWorkflowInstance] = {}

    def now(self) -> datetime:
        """Current workflow-clock time (Temporal durable timer equivalent)."""
        return self._clock()

    def start(self, parcel: ParcelRecord) -> TitlingWorkflowInstance:
        instance = self._workflow.start(parcel)
        self._instances[instance.workflow_id] = instance
        return instance

    def get(self, workflow_id: str) -> TitlingWorkflowInstance:
        try:
            return self._instances[workflow_id]
        except KeyError:
            raise WorkflowError(f"unknown workflow {workflow_id!r}") from None

    def find_by_c_of_o(
        self, tenant_state_id: str, c_of_o_number: str
    ) -> TitlingWorkflowInstance | None:
        """Tenant-scoped lookup of an issued title by its C-of-O number."""
        for instance in self._instances.values():
            if (
                instance.tenant_state_id == tenant_state_id
                and instance.c_of_o_number == c_of_o_number
                and instance.status == WorkflowStatus.ISSUED
            ):
                return instance
        return None

    def advance(
        self,
        workflow_id: str,
        parcel: ParcelRecord,
        *,
        approved: bool,
        actor: str,
        note: str = "",
    ) -> TitlingWorkflowInstance:
        return self._workflow.advance(
            self.get(workflow_id), parcel, approved=approved, actor=actor, note=note
        )

    def run_to_completion(
        self, workflow_id: str, parcel: ParcelRecord, actor_prefix: str = "auto"
    ) -> TitlingWorkflowInstance:
        """Approve every pending stage through to issuance (test convenience)."""
        instance = self.get(workflow_id)
        while (pending := next_pending_stage(instance)) is not None:
            instance = self.advance(
                workflow_id, parcel, approved=True, actor=f"{actor_prefix}:{pending.value.lower()}"
            )
        return instance

    def apply_issuance_to_parcel(self, parcel: ParcelRecord) -> ParcelRecord:
        """Reflect a terminal workflow state onto the parcel registry row."""
        instance = self.get(parcel.titling_workflow_id)
        if instance.status == WorkflowStatus.ISSUED:
            return parcel.model_copy(
                update={
                    "title_type": TitleType.C_OF_O,
                    "c_of_o_number": instance.c_of_o_number,
                }
            )
        if instance.status == WorkflowStatus.REJECTED:
            return parcel.model_copy(update={"status": ParcelStatus.REVOKED})
        return parcel
