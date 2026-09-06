"""pytest suite for mod-ppp-investment: pipeline CRUD, unsolicited proposal
flow, QCBS scoring math (threshold gating + lowest-bid normalization),
concession KPI/settlement lifecycle, disclosure redaction, audit append-only."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    CommercialScore,
    ConcessionContract,
    DocumentSet,
    KPIRecord,
    Milestone,
    Project,
    ProjectStage,
    Proposal,
    StepDownBand,
    TechnicalScore,
)
from app.repo import InMemoryPPPRepository
from app.service import (
    InvalidStateTransition,
    NotFoundError,
    PPPService,
    ThresholdNotMet,
)

ALL_DOCS = [
    "DOC-01", "DOC-02", "DOC-03", "DOC-04", "DOC-05", "DOC-06", "DOC-07", "DOC-08"
]


@pytest.fixture()
def svc():
    return PPPService(InMemoryPPPRepository())


@pytest.fixture()
def client(svc):
    return TestClient(create_app(svc.repo))


def _project(pid="P1", state="taraba", published=False):
    return Project(
        project_id=pid, state_id=state, title="Jalingo Water PPP", sector="WATER",
        description_public="Bulk water supply concession", estimated_value_ngn=12e9,
        sponsoring_agency="TARIPA", published=published,
        internal_notes="confidential negotiation strategy",
        internal_financial_model="IRR model v3.xlsx",
    )


def _proposal(pid="B1", project="P1", state="taraba", unsolicited=False, fee_bps=2_500, docs=ALL_DOCS):
    return Proposal(
        proposal_id=pid, state_id=state, project_id=project, bidder_name=f"Bidder {pid}",
        unsolicited=unsolicited, compliance_docs=list(docs), concession_fee_bps=fee_bps,
    )


def _tech(total_ish: int) -> TechnicalScore:
    # distribute roughly across categories, respecting maxima
    return TechnicalScore(
        architecture=min(100, total_ish // 7), tenancy_ndpa=min(80, total_ish // 9),
        ha_dr_performance=min(70, total_ish // 10), wbs_milestones=min(80, total_ish // 9),
        field_logistics=min(60, total_ish // 12), qa_security_testing=min(60, total_ish // 12),
        key_personnel=min(80, total_ish // 9), local_content=min(70, total_ish // 10),
        past_performance=min(100, total_ish // 7),
    )


PASS_TECH = TechnicalScore(
    architecture=90, tenancy_ndpa=70, ha_dr_performance=60,
    wbs_milestones=70, field_logistics=50, qa_security_testing=55,
    key_personnel=75, local_content=60, past_performance=90,
)  # total = 620 >= 560

FAIL_TECH = TechnicalScore(
    architecture=70, tenancy_ndpa=50, ha_dr_performance=40,
    wbs_milestones=50, field_logistics=40, qa_security_testing=40,
    key_personnel=60, local_content=45, past_performance=60,
)  # total = 455 < 560


def _screened(svc, pid="B1", **kw):
    svc.create_project(_project())
    svc.submit_proposal(_proposal(pid=pid, **kw))
    return svc.screen_proposal(pid, "taraba")


# -- pipeline CRUD + disclosure ------------------------------------------------

def test_pipeline_crud_and_stage_advance(svc):
    svc.create_project(_project())
    p = svc.advance_stage("P1", "taraba", ProjectStage.OBC)
    assert p.stage is ProjectStage.OBC
    with pytest.raises(InvalidStateTransition):
        svc.advance_stage("P1", "taraba", ProjectStage.PIPELINE)  # backwards
    with pytest.raises(NotFoundError):
        svc.advance_stage("GHOST", "taraba", ProjectStage.OBC)


def test_disclosure_redaction(svc):
    svc.create_project(_project(published=True))
    svc.create_project(_project(pid="P2", published=False))
    views = svc.disclosure_view("taraba")
    assert len(views) == 1  # unpublished project withheld
    body = str(views[0])
    assert "Jalingo Water PPP" in body
    assert "confidential" not in body and "IRR model" not in body
    assert "internal_notes" not in views[0] and "internal_financial_model" not in views[0]


def test_public_view_blocked_when_unpublished(svc):
    with pytest.raises(ValueError):
        _project(published=False).public_view()


# -- unsolicited proposal flow (TARIPA guide) ------------------------------------

def test_unsolicited_proposal_flow(svc):
    p = _screened(svc, unsolicited=True)
    assert p.unsolicited and p.status.value == "SCREENING"
    actions = [e.action for e in svc.repo.list_audit("taraba")]
    assert "UNSOLICITED_PROPOSAL_RECEIVED" in actions
    assert "PROPOSAL_SCREENED_IN" in actions


def test_incomplete_compliance_docs_screened_out(svc):
    p = _screened(svc, docs=ALL_DOCS[:-1])  # missing DOC-08
    assert p.status.value == "REJECTED"


def test_screen_twice_rejected(svc):
    _screened(svc)
    with pytest.raises(InvalidStateTransition):
        svc.screen_proposal("B1", "taraba")


# -- QCBS math -------------------------------------------------------------------

def test_technical_totals_and_threshold():
    assert PASS_TECH.total == 620 and PASS_TECH.passes_threshold()
    assert FAIL_TECH.total == 455 and not FAIL_TECH.passes_threshold()
    with pytest.raises(Exception):
        TechnicalScore(**{**PASS_TECH.model_dump(), "architecture": 101})  # over max


def test_commercial_lowest_bid_normalization():
    # lowest bidder scores full weight; higher bids score proportionally less
    low = CommercialScore(lowest_fee_bps=2_000, bid_fee_bps=2_000,
                          capex_commitment=80, stepdown_schedule=40, performance_bond=30)
    assert low.fee_score == 150.0 and low.total == 300.0
    high = CommercialScore(lowest_fee_bps=2_000, bid_fee_bps=2_500,
                           capex_commitment=80, stepdown_schedule=40, performance_bond=30)
    assert high.fee_score == pytest.approx(120.0)  # 2000/2500 * 150
    assert high.total == pytest.approx(270.0)
    # methodology headline: envelope purely on fee → weight 300
    pure = CommercialScore(lowest_fee_bps=2_000, bid_fee_bps=4_000,
                           capex_commitment=0, stepdown_schedule=0, performance_bond=0,
                           fee_weight=300)
    assert pure.fee_score == pytest.approx(150.0)  # S_comm = 2000/4000 * 300


def test_threshold_gating_blocks_commercial(svc):
    _screened(svc)
    svc.record_technical("B1", "taraba", FAIL_TECH)
    # rejected at threshold — commercial envelope stays sealed
    assert svc.repo.get_proposal("B1").status.value == "REJECTED"
    with pytest.raises(ThresholdNotMet):
        svc.open_commercial("B1", "taraba", CommercialScore(
            lowest_fee_bps=2_500, bid_fee_bps=2_500,
            capex_commitment=0, stepdown_schedule=0, performance_bond=0))


def test_full_qcbs_path_to_award(svc):
    _screened(svc)
    svc.record_technical("B1", "taraba", PASS_TECH)
    ev = svc.open_commercial("B1", "taraba", CommercialScore(
        lowest_fee_bps=2_500, bid_fee_bps=2_500,
        capex_commitment=70, stepdown_schedule=35, performance_bond=25))
    assert ev.composite == pytest.approx(620 + 150 + 130)  # 900 / 1000
    awarded = svc.award("B1", "taraba")
    assert awarded.status.value == "AWARDED"


def test_commercial_must_match_sealed_bid(svc):
    _screened(svc)
    svc.record_technical("B1", "taraba", PASS_TECH)
    with pytest.raises(InvalidStateTransition):
        svc.open_commercial("B1", "taraba", CommercialScore(
            lowest_fee_bps=2_500, bid_fee_bps=3_000,  # bid was 2500
            capex_commitment=0, stepdown_schedule=0, performance_bond=0))


# -- OBC/FBC --------------------------------------------------------------------

def test_documentset_tracking(svc):
    svc.create_project(_project())
    ds = svc.upsert_documentset(DocumentSet(
        documentset_id="DS1", state_id="taraba", project_id="P1",
        stage=ProjectStage.OBC, documents={"strategic_case": "APPROVED", "economic_case": "SUBMITTED"}))
    assert ds.stage is ProjectStage.OBC
    with pytest.raises(ValueError):
        svc.upsert_documentset(DocumentSet(
            documentset_id="DS2", state_id="taraba", project_id="P1",
            stage=ProjectStage.PIPELINE, documents={}))


# -- concession lifecycle ----------------------------------------------------------

def _contract(svc):
    _screened(svc)
    svc.record_technical("B1", "taraba", PASS_TECH)
    svc.open_commercial("B1", "taraba", CommercialScore(
        lowest_fee_bps=2_500, bid_fee_bps=2_500,
        capex_commitment=70, stepdown_schedule=35, performance_bond=25))
    svc.award("B1", "taraba")
    c = ConcessionContract(
        contract_id="C1", state_id="taraba", project_id="P1", proposal_id="B1",
        concessionaire="Bidder B1", term_months=120, revenue_share_state_bps=2_500,
        milestones=[Milestone(name="financial_close", due=datetime(2026, 6, 1, tzinfo=timezone.utc))])
    return svc.sign_contract(c)


def test_contract_requires_award(svc):
    _screened(svc)
    c = ConcessionContract(
        contract_id="C0", state_id="taraba", project_id="P1", proposal_id="B1",
        concessionaire="X", term_months=60, revenue_share_state_bps=2_000)
    with pytest.raises(InvalidStateTransition):
        svc.sign_contract(c)


def test_kpi_and_settlement_lifecycle(svc):
    _contract(svc)
    svc.record_kpi(KPIRecord(kpi_id="K1", state_id="taraba", contract_id="C1",
                             period="2026-07", collections_kobo=100_000_000,
                             uptime_pct=99.5, sla_breaches=0))
    with pytest.raises(InvalidStateTransition):  # duplicate period
        svc.record_kpi(KPIRecord(kpi_id="K2", state_id="taraba", contract_id="C1",
                                 period="2026-07", collections_kobo=1, uptime_pct=50, sla_breaches=3))
    st = svc.reconcile_month("C1", "taraba", "2026-07")
    assert st.state_share_kobo == 25_000_000  # 25% of 100M kobo
    assert st.concessionaire_share_kobo == 75_000_000
    assert st.reconciled
    with pytest.raises(InvalidStateTransition):  # append-only, no re-reconcile
        svc.reconcile_month("C1", "taraba", "2026-07")


def test_reconcile_requires_kpi(svc):
    _contract(svc)
    with pytest.raises(InvalidStateTransition):
        svc.reconcile_month("C1", "taraba", "2026-09")


def test_stepdown_schedule_evaluation(svc):
    _contract(svc)
    bands = [StepDownBand(threshold_kobo=150_000_000, state_share_bps=3_000),
             StepDownBand(threshold_kobo=400_000_000, state_share_bps=3_500)]
    svc.record_kpi(KPIRecord(kpi_id="K1", state_id="taraba", contract_id="C1",
                             period="2026-07", collections_kobo=100_000_000, uptime_pct=99.0, sla_breaches=0))
    st1 = svc.reconcile_month("C1", "taraba", "2026-07", bands)
    assert st1.applied_state_share_bps == 2_500  # below first threshold
    svc.record_kpi(KPIRecord(kpi_id="K2", state_id="taraba", contract_id="C1",
                             period="2026-08", collections_kobo=100_000_000, uptime_pct=99.0, sla_breaches=0))
    st2 = svc.reconcile_month("C1", "taraba", "2026-08", bands)
    assert st2.applied_state_share_bps == 3_000  # cumulative ₦2.0m kobo ≥ threshold → state share steps up
    assert st2.state_share_kobo == 30_000_000


def test_milestones(svc):
    c = _contract(svc)
    c2 = svc.complete_milestone("C1", "taraba", "financial_close")
    assert c2.milestones[0].completed_at is not None
    with pytest.raises(NotFoundError):
        svc.complete_milestone("C1", "taraba", "nonexistent")


# -- tenant isolation + audit -----------------------------------------------------

def test_tenant_isolation(svc):
    _screened(svc)
    with pytest.raises(Exception):
        svc.record_technical("B1", "nasarawa", PASS_TECH)


def test_audit_append_only_and_integrity(svc):
    _contract(svc)
    assert svc.verify_audit_chain() is True
    svc.repo._audit[2].details = "tampered"
    assert svc.verify_audit_chain() is False


def test_http_disclosure_and_audit(client):
    client.post("/projects", json={
        "project_id": "P1", "state_id": "taraba", "title": "Water PPP", "sector": "WATER",
        "description_public": "desc", "sponsoring_agency": "TARIPA",
        "internal_notes": "secret"})
    r = client.get("/disclosure", params={"state_id": "taraba"})
    assert r.json() == []  # unpublished
    client.post("/projects/P1/publish", params={"state_id": "taraba"})
    r = client.get("/disclosure", params={"state_id": "taraba"})
    assert len(r.json()) == 1 and "secret" not in r.text
    assert client.get("/audit/verify").json() == {"chain_valid": True}
