"""mod-ppp-investment FastAPI application."""
from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .models import (
    AuditEntry,
    CommercialScore,
    ConcessionContract,
    DocumentSet,
    Evaluation,
    KPIRecord,
    Project,
    ProjectStage,
    Proposal,
    SettlementStatement,
    StepDownBand,
    TechnicalScore,
)
from .repo import InMemoryPPPRepository, PPPRepository
from .service import (
    InvalidStateTransition,
    NotFoundError,
    PPPService,
    TenantIsolationError,
    ThresholdNotMet,
)


class StageAdvanceRequest(BaseModel):
    state_id: str
    stage: ProjectStage


class ReconcileRequest(BaseModel):
    state_id: str
    period: str
    bands: List[StepDownBand] = []


# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None


def create_app(repo: Optional[PPPRepository] = None) -> FastAPI:
    app = FastAPI(title="mod-ppp-investment — PPP Pipeline, QCBS & Concession Monitoring")
    app.state.repo = repo or InMemoryPPPRepository()

    def service(request: Request) -> PPPService:
        return PPPService(request.app.state.repo)

    def _errors(fn):
        try:
            return fn()
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except TenantIsolationError as exc:
            raise HTTPException(403, str(exc))
        except ThresholdNotMet as exc:
            raise HTTPException(409, str(exc))
        except InvalidStateTransition as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/projects", response_model=Project, status_code=201)
    def create_project(project: Project, svc: PPPService = Depends(service)):
        return svc.create_project(project)

    @app.get("/projects", response_model=list[Project])
    def list_projects(request: Request, state_id: Optional[str] = None):
        return request.app.state.repo.list_projects(state_id)

    @app.post("/projects/{project_id}/stage", response_model=Project)
    def advance(project_id: str, body: StageAdvanceRequest, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.advance_stage(project_id, body.state_id, body.stage))

    @app.post("/projects/{project_id}/publish", response_model=Project)
    def publish(project_id: str, state_id: str, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.publish_project(project_id, state_id))

    @app.get("/disclosure")
    def disclosure(svc: PPPService = Depends(service), state_id: Optional[str] = None):
        """Public disclosure view — publishable subset only."""
        return svc.disclosure_view(state_id)

    @app.post("/proposals", response_model=Proposal, status_code=201)
    def submit_proposal(proposal: Proposal, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.submit_proposal(proposal))

    @app.post("/proposals/{proposal_id}/screen", response_model=Proposal)
    def screen(proposal_id: str, state_id: str, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.screen_proposal(proposal_id, state_id))

    @app.post("/proposals/{proposal_id}/technical", response_model=Evaluation)
    def technical(proposal_id: str, state_id: str, score: TechnicalScore, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.record_technical(proposal_id, state_id, score))

    @app.post("/proposals/{proposal_id}/commercial", response_model=Evaluation)
    def commercial(proposal_id: str, state_id: str, score: CommercialScore, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.open_commercial(proposal_id, state_id, score))

    @app.post("/proposals/{proposal_id}/award", response_model=Proposal)
    def award(proposal_id: str, state_id: str, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.award(proposal_id, state_id))

    @app.post("/documentsets", response_model=DocumentSet, status_code=201)
    def documentset(ds: DocumentSet, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.upsert_documentset(ds))

    @app.post("/contracts", response_model=ConcessionContract, status_code=201)
    def sign(contract: ConcessionContract, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.sign_contract(contract))

    @app.post("/contracts/{contract_id}/milestones/{name}/complete", response_model=ConcessionContract)
    def milestone(contract_id: str, name: str, state_id: str, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.complete_milestone(contract_id, state_id, name))

    @app.post("/kpis", response_model=KPIRecord, status_code=201)
    def kpi(k: KPIRecord, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.record_kpi(k))

    @app.post("/contracts/{contract_id}/reconcile", response_model=SettlementStatement, status_code=201)
    def reconcile(contract_id: str, body: ReconcileRequest, svc: PPPService = Depends(service)):
        return _errors(lambda: svc.reconcile_month(contract_id, body.state_id, body.period, body.bands))

    @app.get("/audit", response_model=list[AuditEntry])
    def audit(request: Request, state_id: Optional[str] = None):
        return request.app.state.repo.list_audit(state_id)

    @app.get("/audit/verify")
    def audit_integrity(svc: PPPService = Depends(service)):
        return {"chain_valid": svc.verify_audit_chain()}

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-ppp-investment"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-ppp-investment")
    return app


app = create_app()
