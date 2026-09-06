"""Service layer for mod-ppp-investment: pipeline, proposals, QCBS, OBC/FBC,
concession KPI/settlement lifecycle, and the append-only audit chain.

QCBS math per docs/procurement/evaluation-scorecards.md [LIVE]:
  composite (1000) = technical (700, pass ≥ 560) + commercial (300)
  S_comm = (lowest acceptable fee % / bid fee %) × fee_weight   [lowest-bid
  normalization — the lowest acceptable bid scores the full weight]
Threshold gating: commercial envelope opens only when technical ≥ 560.
"""
from __future__ import annotations

import itertools
from typing import List, Optional

from .models import (
    AuditEntry,
    CommercialScore,
    ConcessionContract,
    DocumentSet,
    Evaluation,
    KPIRecord,
    Milestone,
    Project,
    ProjectStage,
    Proposal,
    ProposalStatus,
    SettlementStatement,
    StepDownBand,
    TechnicalScore,
    utcnow,
)
from .repo import PPPRepository


class NotFoundError(Exception):
    pass


class InvalidStateTransition(Exception):
    pass


class ThresholdNotMet(Exception):
    """Commercial envelope opened while technical score < 560."""


class TenantIsolationError(Exception):
    pass


TECHNICAL_THRESHOLD = 560


class PPPService:
    def __init__(self, repo: PPPRepository) -> None:
        self.repo = repo
        self._ids = itertools.count(1)

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):06d}"

    def _audit(self, action, state_id, actor_id, subject_id, details="") -> AuditEntry:
        prev = self.repo.audit_tail_hash()
        seq = len(self.repo.list_audit()) + 1
        payload = f"{seq}|{action}|{state_id}|{actor_id}|{subject_id}|{details}"
        entry = AuditEntry(
            seq=seq, action=action, state_id=state_id, actor_id=actor_id,
            subject_id=subject_id, details=details, prev_hash=prev,
            entry_hash=AuditEntry.compute_hash(prev, payload),
        )
        return self.repo.append_audit(entry)

    @staticmethod
    def _require_tenant(record_state: str, state_id: str, what: str) -> None:
        if record_state != state_id:
            raise TenantIsolationError(f"{what} belongs to tenant {record_state!r}, not {state_id!r}")

    # -- pipeline registry ---------------------------------------------------
    def create_project(self, project: Project) -> Project:
        saved = self.repo.save_project(project)
        self._audit("PROJECT_CREATED", project.state_id, "agency", project.project_id, project.stage.value)
        return saved

    def advance_stage(self, project_id: str, state_id: str, stage: ProjectStage) -> Project:
        project = self.repo.get_project(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id!r} not found")
        self._require_tenant(project.state_id, state_id, "project")
        order = list(ProjectStage)
        if order.index(stage) <= order.index(project.stage):
            raise InvalidStateTransition(f"cannot move {project.stage.value} -> {stage.value}")
        project.stage = stage
        self.repo.save_project(project)
        self._audit("PROJECT_STAGE_ADVANCED", state_id, "agency", project_id, stage.value)
        return project

    def publish_project(self, project_id: str, state_id: str) -> Project:
        project = self.repo.get_project(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id!r} not found")
        self._require_tenant(project.state_id, state_id, "project")
        project.published = True
        self.repo.save_project(project)
        self._audit("PROJECT_PUBLISHED", state_id, "agency", project_id)
        return project

    def disclosure_view(self, state_id: Optional[str] = None) -> List[dict]:
        """Public disclosure: publishable subset only, internal fields redacted."""
        return [p.public_view() for p in self.repo.list_projects(state_id) if p.published]

    # -- proposal intake (incl. unsolicited, TARIPA guide flow) ---------------
    def submit_proposal(self, proposal: Proposal) -> Proposal:
        project = self.repo.get_project(proposal.project_id)
        if project is None:
            raise NotFoundError(f"project {proposal.project_id!r} not found")
        self._require_tenant(project.state_id, proposal.state_id, "project")
        saved = self.repo.save_proposal(proposal)
        action = "UNSOLICITED_PROPOSAL_RECEIVED" if proposal.unsolicited else "PROPOSAL_RECEIVED"
        self._audit(action, proposal.state_id, proposal.bidder_name, proposal.proposal_id)
        return saved

    def screen_proposal(self, proposal_id: str, state_id: str) -> Proposal:
        """Screening gate (unsolicited-guide flow): compliance docs DOC-01..08."""
        proposal = self._proposal(proposal_id, state_id)
        if proposal.status is not ProposalStatus.RECEIVED:
            raise InvalidStateTransition(f"proposal is {proposal.status.value}, not RECEIVED")
        proposal.status = ProposalStatus.SCREENING
        if not proposal.compliance_complete():
            proposal.status = ProposalStatus.REJECTED
            self._audit("PROPOSAL_SCREENED_OUT", state_id, "agency", proposal_id, "incomplete compliance docs")
        else:
            self._audit("PROPOSAL_SCREENED_IN", state_id, "agency", proposal_id)
        self.repo.save_proposal(proposal)
        return proposal

    def _proposal(self, proposal_id: str, state_id: str) -> Proposal:
        proposal = self.repo.get_proposal(proposal_id)
        if proposal is None:
            raise NotFoundError(f"proposal {proposal_id!r} not found")
        self._require_tenant(proposal.state_id, state_id, "proposal")
        return proposal

    # -- QCBS evaluation -------------------------------------------------------
    def record_technical(self, proposal_id: str, state_id: str, technical: TechnicalScore) -> Evaluation:
        proposal = self._proposal(proposal_id, state_id)
        if proposal.status is not ProposalStatus.SCREENING:
            raise InvalidStateTransition("proposal must pass screening before evaluation")
        evaluation = Evaluation(
            evaluation_id=self._next_id("EV"), state_id=state_id,
            proposal_id=proposal_id, technical=technical,
        )
        self.repo.save_evaluation(evaluation)
        self._audit(
            "TECHNICAL_EVALUATED", state_id, "evaluator", proposal_id,
            f"technical={technical.total}/700 threshold_met={technical.passes_threshold()}",
        )
        if not technical.passes_threshold():
            proposal.status = ProposalStatus.REJECTED
            self.repo.save_proposal(proposal)
            self._audit("PROPOSAL_REJECTED_THRESHOLD", state_id, "evaluator", proposal_id)
        return evaluation

    def open_commercial(self, proposal_id: str, state_id: str, commercial: CommercialScore) -> Evaluation:
        """Open the commercial envelope — gated on technical ≥ 560."""
        proposal = self._proposal(proposal_id, state_id)
        evaluation = self.repo.get_evaluation(proposal_id)
        if evaluation is None:
            raise InvalidStateTransition("no technical evaluation on record")
        if not evaluation.technical.passes_threshold():
            raise ThresholdNotMet(
                f"technical {evaluation.technical.total} < {TECHNICAL_THRESHOLD}; commercial envelope stays sealed"
            )
        if proposal.concession_fee_bps is None:
            raise InvalidStateTransition("proposal carries no concession fee bid")
        if commercial.bid_fee_bps != proposal.concession_fee_bps:
            raise InvalidStateTransition("commercial envelope fee does not match the sealed bid")
        evaluation.commercial = commercial
        proposal.status = ProposalStatus.EVALUATED
        self.repo.save_evaluation(evaluation)
        self.repo.save_proposal(proposal)
        self._audit(
            "COMMERCIAL_EVALUATED", state_id, "evaluator", proposal_id,
            f"commercial={commercial.total:.1f}/300 composite={evaluation.composite:.1f}/1000",
        )
        return evaluation

    def award(self, proposal_id: str, state_id: str) -> Proposal:
        proposal = self._proposal(proposal_id, state_id)
        if proposal.status is not ProposalStatus.EVALUATED:
            raise InvalidStateTransition("only evaluated proposals can be awarded")
        proposal.status = ProposalStatus.AWARDED
        self.repo.save_proposal(proposal)
        self._audit("PROPOSAL_AWARDED", state_id, "agency", proposal_id)
        return proposal

    # -- OBC/FBC document sets ---------------------------------------------------
    def upsert_documentset(self, ds: DocumentSet) -> DocumentSet:
        project = self.repo.get_project(ds.project_id)
        if project is None:
            raise NotFoundError(f"project {ds.project_id!r} not found")
        self._require_tenant(project.state_id, ds.state_id, "project")
        if ds.stage not in (ProjectStage.OBC, ProjectStage.FBC):
            raise ValueError("document sets track OBC/FBC stages only")
        ds.updated_at = utcnow()
        saved = self.repo.save_documentset(ds)
        self._audit("DOCUMENTSET_UPDATED", ds.state_id, "agency", ds.documentset_id, ds.stage.value)
        return saved

    # -- concession contracts ------------------------------------------------------
    def sign_contract(self, contract: ConcessionContract) -> ConcessionContract:
        proposal = self._proposal(contract.proposal_id, contract.state_id)
        if proposal.status is not ProposalStatus.AWARDED:
            raise InvalidStateTransition("contract requires an awarded proposal")
        project = self.repo.get_project(contract.project_id)
        self._require_tenant(project.state_id, contract.state_id, "project")
        project.stage = ProjectStage.IN_CONCESSION
        self.repo.save_project(project)
        saved = self.repo.save_contract(contract)
        self._audit("CONTRACT_SIGNED", contract.state_id, "agency", contract.contract_id, contract.concessionaire)
        return saved

    def complete_milestone(self, contract_id: str, state_id: str, name: str) -> ConcessionContract:
        contract = self.repo.get_contract(contract_id)
        if contract is None:
            raise NotFoundError(f"contract {contract_id!r} not found")
        self._require_tenant(contract.state_id, state_id, "contract")
        for m in contract.milestones:
            if m.name == name and m.completed_at is None:
                m.completed_at = utcnow()
                self.repo.save_contract(contract)
                self._audit("MILESTONE_COMPLETED", state_id, "agency", contract_id, name)
                return contract
        raise NotFoundError(f"open milestone {name!r} not found on {contract_id!r}")

    # -- KPI + settlement lifecycle ------------------------------------------------
    def record_kpi(self, kpi: KPIRecord) -> KPIRecord:
        contract = self.repo.get_contract(kpi.contract_id)
        if contract is None:
            raise NotFoundError(f"contract {kpi.contract_id!r} not found")
        self._require_tenant(contract.state_id, kpi.state_id, "contract")
        if any(k.period == kpi.period for k in self.repo.list_kpis(kpi.contract_id)):
            raise InvalidStateTransition(f"KPI for period {kpi.period} already recorded")
        saved = self.repo.save_kpi(kpi)
        self._audit("KPI_RECORDED", kpi.state_id, "monitor", kpi.contract_id, kpi.period)
        return saved

    @staticmethod
    def effective_state_share_bps(base_bps: int, bands: List[StepDownBand], cumulative_kobo: int) -> int:
        """Evaluate the step-down schedule [policy-pack guardrail]: the state
        share steps UP to the band whose threshold cumulative collections have
        passed (fee taper as IGR baselines are exceeded)."""
        effective = base_bps
        for band in sorted(bands, key=lambda b: b.threshold_kobo):
            if cumulative_kobo >= band.threshold_kobo:
                effective = max(effective, band.state_share_bps)
        return effective

    def reconcile_month(
        self,
        contract_id: str,
        state_id: str,
        period: str,
        bands: Optional[List[StepDownBand]] = None,
    ) -> SettlementStatement:
        """Monthly reconciliation: gross collections from the KPI record split
        into state / concessionaire legs at the step-down-adjusted share.
        Each leg maps to a TigerBeetle transfer (state → 3001 code 101;
        concessionaire → 2099 code 103)."""
        contract = self.repo.get_contract(contract_id)
        if contract is None:
            raise NotFoundError(f"contract {contract_id!r} not found")
        self._require_tenant(contract.state_id, state_id, "contract")
        kpi = next((k for k in self.repo.list_kpis(contract_id) if k.period == period), None)
        if kpi is None:
            raise InvalidStateTransition(f"no KPI record for period {period}")
        if any(s.period == period for s in self.repo.list_statements(contract_id)):
            raise InvalidStateTransition(f"statement for {period} already reconciled (append-only)")
        prior = sum(s.gross_collections_kobo for s in self.repo.list_statements(contract_id))
        cumulative = prior + kpi.collections_kobo
        bps = self.effective_state_share_bps(contract.revenue_share_state_bps, bands or [], cumulative)
        state_amt = kpi.collections_kobo * bps // 10_000
        statement = SettlementStatement(
            statement_id=self._next_id("STM"),
            state_id=state_id,
            contract_id=contract_id,
            period=period,
            gross_collections_kobo=kpi.collections_kobo,
            state_share_kobo=state_amt,
            concessionaire_share_kobo=kpi.collections_kobo - state_amt,
            applied_state_share_bps=bps,
            reconciled=True,
        )
        saved = self.repo.save_statement(statement)
        self._audit(
            "SETTLEMENT_RECONCILED", state_id, "monitor", contract_id,
            f"{period}: gross={kpi.collections_kobo} state_bps={bps}",
        )
        return saved

    # -- audit integrity -----------------------------------------------------------
    def verify_audit_chain(self) -> bool:
        from .repo import InMemoryPPPRepository

        prev = InMemoryPPPRepository.GENESIS_HASH
        for entry in self.repo.list_audit():
            payload = f"{entry.seq}|{entry.action}|{entry.state_id}|{entry.actor_id}|{entry.subject_id}|{entry.details}"
            if entry.prev_hash != prev:
                return False
            if entry.entry_hash != AuditEntry.compute_hash(prev, payload):
                return False
            prev = entry.entry_hash
        return True
