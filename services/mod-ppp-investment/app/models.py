"""Domain models for mod-ppp-investment (SOS module g — PPP & Investment).

Entry-gift module for **TARIPA** (Taraba published pipeline + unsolicited
proposal guide [LIVE], docs/states/taraba.md) and **NASIDA** (Nasarawa PPP
law, UKNIAF-supported manual, ₦212bn+ pipeline [LIVE], docs/states/nasarawa.md).

QCBS scoring implements the 1,000-point model of
``docs/procurement/evaluation-scorecards.md``: Technical 700 (pass ≥ 560),
Commercial 300 with S_comm = (lowest concession fee % / bid fee %) × weight.

Monetary amounts are integer kobo; percentages are basis points (bps).
"""
from __future__ import annotations

import enum
import hashlib
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATE_IDS = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")

# Bid compliance checklist from docs/procurement/evaluation-scorecards.md [LIVE].
REQUIRED_COMPLIANCE_DOCS = (
    "DOC-01 Certificate of Incorporation & CAC status report",
    "DOC-02 3-year FIRS Tax Clearance Certificate",
    "DOC-03 PENCOM, ITF & NSITF compliance certificates",
    "DOC-04 NDPC Data Protection Compliance License",
    "DOC-05 ISO/IEC 27001 & ISO 22301 certificates",
    "DOC-06 Technical architecture proposal & WBS plan",
    "DOC-07 Commercial concession fee & IRR model",
    "DOC-08 Tier-1 bank performance guarantee letter",
)


class ProjectStage(str, enum.Enum):
    PIPELINE = "PIPELINE"
    OBC = "OBC"  # Outline Business Case
    FBC = "FBC"  # Full Business Case
    PROCUREMENT = "PROCUREMENT"
    AWARDED = "AWARDED"
    IN_CONCESSION = "IN_CONCESSION"
    CLOSED = "CLOSED"


class ProposalStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    SCREENING = "SCREENING"
    EVALUATED = "EVALUATED"
    AWARDED = "AWARDED"
    REJECTED = "REJECTED"


class Project(BaseModel):
    """Pipeline registry entry. ``public_view()`` returns the publishable
    subset (TARIPA published pipeline [LIVE]); internal fields never leave."""

    project_id: str
    state_id: str
    title: str
    sector: str = Field(description="e.g. LAND, TRANSPORT, ENERGY, WATER")
    description_public: str
    estimated_value_ngn: Optional[float] = None  # publishable headline
    stage: ProjectStage = ProjectStage.PIPELINE
    sponsoring_agency: str = Field(description="e.g. TARIPA, NASIDA, Ogun PPP Office")
    published: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    # --- internal-only (redacted from disclosure view) ---
    internal_notes: str = ""
    internal_financial_model: Optional[str] = None

    def public_view(self) -> dict:
        if not self.published:
            raise ValueError(f"project {self.project_id!r} is not published for disclosure")
        return {
            "project_id": self.project_id,
            "state_id": self.state_id,
            "title": self.title,
            "sector": self.sector,
            "description": self.description_public,
            "estimated_value_ngn": self.estimated_value_ngn,
            "stage": self.stage.value,
            "sponsoring_agency": self.sponsoring_agency,
        }


class Proposal(BaseModel):
    """Solicited or unsolicited (TARIPA-style published guide flow) proposal."""

    proposal_id: str
    state_id: str
    project_id: str
    bidder_name: str
    unsolicited: bool = False
    status: ProposalStatus = ProposalStatus.RECEIVED
    submitted_at: datetime = Field(default_factory=utcnow)
    compliance_docs: list[str] = Field(default_factory=list)
    concession_fee_bps: Optional[int] = Field(
        default=None, ge=0, description="bid revenue-share / concession fee, basis points"
    )

    def compliance_complete(self) -> bool:
        submitted = {d.split(" ", 1)[0] for d in self.compliance_docs}
        return all(d.split(" ", 1)[0] in submitted for d in REQUIRED_COMPLIANCE_DOCS)


class TechnicalScore(BaseModel):
    """Technical envelope, max 700; passing threshold 560 (80%) [LIVE]."""

    # Category 1 — Technical Architecture & Design (250)
    architecture: int = Field(ge=0, le=100)
    tenancy_ndpa: int = Field(ge=0, le=80)
    ha_dr_performance: int = Field(ge=0, le=70)
    # Category 2 — Implementation & Delivery Plan (200)
    wbs_milestones: int = Field(ge=0, le=80)
    field_logistics: int = Field(ge=0, le=60)
    qa_security_testing: int = Field(ge=0, le=60)
    # Category 3 — Human Capital & Local Content (150)
    key_personnel: int = Field(ge=0, le=80)
    local_content: int = Field(ge=0, le=70)
    # Category 4 — Past Performance (100)
    past_performance: int = Field(ge=0, le=100)

    @property
    def total(self) -> int:
        return (
            self.architecture + self.tenancy_ndpa + self.ha_dr_performance
            + self.wbs_milestones + self.field_logistics + self.qa_security_testing
            + self.key_personnel + self.local_content + self.past_performance
        )

    def passes_threshold(self) -> bool:
        return self.total >= 560


class CommercialScore(BaseModel):
    """Commercial envelope, max 300.

    S_comm = (lowest acceptable concession fee % / bid fee %) × fee weight.
    Per the scorecard breakdown the fee component carries 150 of 300; the
    remaining 150 split as CapEx 80 / step-down 40 / performance bond 30.
    """

    lowest_fee_bps: int = Field(gt=0)
    bid_fee_bps: int = Field(gt=0)
    capex_commitment: int = Field(ge=0, le=80)
    stepdown_schedule: int = Field(ge=0, le=40)
    performance_bond: int = Field(ge=0, le=30)
    fee_weight: int = Field(default=150, ge=1, le=300)

    @property
    def fee_score(self) -> float:
        return self.lowest_fee_bps / self.bid_fee_bps * self.fee_weight

    @property
    def total(self) -> float:
        return self.fee_score + self.capex_commitment + self.stepdown_schedule + self.performance_bond


class Evaluation(BaseModel):
    """QCBS evaluation record. The commercial envelope may only be attached
    when the technical envelope passes 560 — enforced by the service layer."""

    evaluation_id: str
    state_id: str
    proposal_id: str
    technical: TechnicalScore
    commercial: Optional[CommercialScore] = None
    evaluated_at: datetime = Field(default_factory=utcnow)

    @property
    def composite(self) -> float:
        return self.technical.total + (self.commercial.total if self.commercial else 0.0)


class DocumentSet(BaseModel):
    """OBC/FBC document-set tracking for a project stage."""

    documentset_id: str
    state_id: str
    project_id: str
    stage: ProjectStage = Field(description="OBC or FBC")
    documents: dict[str, str] = Field(
        default_factory=dict, description="doc name -> status (SUBMITTED/APPROVED/MISSING)"
    )
    updated_at: datetime = Field(default_factory=utcnow)


class Milestone(BaseModel):
    name: str
    due: datetime
    completed_at: Optional[datetime] = None
    verified: bool = False


class ConcessionContract(BaseModel):
    contract_id: str
    state_id: str
    project_id: str
    proposal_id: str
    concessionaire: str
    signed_at: datetime = Field(default_factory=utcnow)
    term_months: int = Field(gt=0)
    revenue_share_state_bps: int = Field(ge=0, le=10_000)
    milestones: list[Milestone] = Field(default_factory=list)
    active: bool = True


class KPIRecord(BaseModel):
    """Monthly performance KPI monitoring record per concession."""

    kpi_id: str
    state_id: str
    contract_id: str
    period: str = Field(description="YYYY-MM")
    collections_kobo: int = Field(ge=0)
    uptime_pct: float = Field(ge=0, le=100)
    sla_breaches: int = Field(ge=0)
    recorded_at: datetime = Field(default_factory=utcnow)


class StepDownBand(BaseModel):
    """Step-down schedule band [policy-pack guardrail]: once cumulative
    reconciled collections exceed ``threshold_kobo``, the state revenue share
    steps UP to ``state_share_bps`` (concession fee tapers as IGR baseline is
    exceeded — docs/procurement/evaluation-scorecards.md, 'Tapering')."""

    threshold_kobo: int = Field(ge=0)
    state_share_bps: int = Field(ge=0, le=10_000)


class SettlementStatement(BaseModel):
    """Monthly revenue-share reconciliation per concession (double-entry legs
    wired to TigerBeetle: state share → 3001 TSA code 101; concessionaire
    share → 2099 escrow code 103)."""

    statement_id: str
    state_id: str
    contract_id: str
    period: str
    gross_collections_kobo: int = Field(ge=0)
    state_share_kobo: int = Field(ge=0)
    concessionaire_share_kobo: int = Field(ge=0)
    applied_state_share_bps: int = Field(ge=0, le=10_000)
    reconciled: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class AuditEntry(BaseModel):
    """Procurement-integrity append-only, hash-chained audit record
    (ICRC-compliant documentation from day one)."""

    seq: int
    action: str
    state_id: str
    actor_id: str
    subject_id: str
    details: str = ""
    prev_hash: str
    entry_hash: str
    at: datetime = Field(default_factory=utcnow)

    @staticmethod
    def compute_hash(prev_hash: str, payload: str) -> str:
        return hashlib.sha256((prev_hash + "|" + payload).encode()).hexdigest()
