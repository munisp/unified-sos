"""mod-mining FastAPI application."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from .audit import AuditLog
from .bus import EventBus, InMemoryEventBus
from .levy import SplitRule
from .models import (
    AssayRecord,
    Consignment,
    ConsignmentStatus,
    MineralSite,
    WeighbridgeReading,
)
from .repo import InMemoryMiningRepository, MiningRepository
from .service import InvalidStateTransition, MiningService, NotFoundError


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


def create_app(
    repo: Optional[MiningRepository] = None, bus: Optional[EventBus] = None
) -> FastAPI:
    app = FastAPI(title="mod-mining — Solid Minerals Custody & Levies")
    app.state.repo = repo or InMemoryMiningRepository()
    app.state.bus = bus or InMemoryEventBus()
    app.state.audit = AuditLog()

    def service(request: Request) -> MiningService:
        return MiningService(request.app.state.repo, request.app.state.bus)

    @app.get("/api/v1/states/{state_id}/audit/verify")
    def verify_audit(state_id: str, request: Request):
        """Hash-chain integrity check; entries scoped to the tenant."""
        audit: AuditLog = request.app.state.audit
        return {
            "valid": audit.verify() == [],
            "entries": len(audit.events(tenant_state_id=state_id)),
        }

    @app.post("/sites", response_model=MineralSite, status_code=201)
    def register_site(site: MineralSite, svc: MiningService = Depends(service),
                      request: Request = None):
        created = svc.register_site(site)
        request.app.state.audit.record(
            "mining.site_registered", created.state_id,
            detail={"site_id": created.site_id, "minerals": len(created.minerals)})
        return created

    @app.get("/sites/{site_id}", response_model=MineralSite)
    def get_site(site_id: str, request: Request):
        site = request.app.state.repo.get_site(site_id)
        if site is None:
            raise HTTPException(404, f"site {site_id!r} not found")
        return site

    @app.post("/consignments", response_model=Consignment, status_code=201)
    def create_consignment(
        consignment: Consignment, svc: MiningService = Depends(service),
        request: Request = None,
    ):
        try:
            created = svc.create_consignment(consignment)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        request.app.state.audit.record(
            "mining.consignment_created", created.state_id,
            detail={"consignment_id": created.consignment_id})
        return created

    @app.get("/consignments", response_model=list[Consignment])
    def list_consignments(request: Request, state_id: Optional[str] = None):
        return request.app.state.repo.list_consignments(state_id)

    @app.get("/consignments/{consignment_id}", response_model=Consignment)
    def get_consignment(consignment_id: str, request: Request):
        con = request.app.state.repo.get_consignment(consignment_id)
        if con is None:
            raise HTTPException(404, f"consignment {consignment_id!r} not found")
        return con

    @app.post("/consignments/{consignment_id}/weighbridge", response_model=Consignment)
    def weighbridge(
        consignment_id: str,
        reading: WeighbridgeReading,
        svc: MiningService = Depends(service),
    ):
        try:
            return svc.record_weighbridge(consignment_id, reading)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except (InvalidStateTransition, ValueError) as exc:
            raise HTTPException(409, str(exc))

    @app.post("/consignments/{consignment_id}/assay", response_model=Consignment)
    def assay(
        consignment_id: str,
        assay: AssayRecord,
        svc: MiningService = Depends(service),
    ):
        try:
            return svc.attach_assay(consignment_id, assay)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except (InvalidStateTransition, ValueError) as exc:
            raise HTTPException(409, str(exc))

    @app.post("/consignments/{consignment_id}/dispatch", response_model=Consignment)
    def dispatch(consignment_id: str, svc: MiningService = Depends(service)):
        try:
            return svc.dispatch(consignment_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except (InvalidStateTransition, ValueError) as exc:
            raise HTTPException(409, str(exc))

    @app.post("/consignments/{consignment_id}/deliver", response_model=Consignment)
    def deliver(consignment_id: str, svc: MiningService = Depends(service)):
        try:
            return svc.record_delivery(consignment_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except InvalidStateTransition as exc:
            raise HTTPException(409, str(exc))

    @app.post("/split-rules/validate", status_code=200)
    def validate_split_rule(rule: SplitRule):
        """Dry-run validation of a split rule against the royalty constraint.

        Constructing a :class:`SplitRule` raises RoyaltyConstraintViolation
        (a ValueError) for any royalty/federal claim; FastAPI maps that to
        422 automatically.
        """
        return {"valid": True, "beneficiary": rule.beneficiary}

    @app.get("/events")
    def published_events(request: Request):
        bus = request.app.state.bus
        return getattr(bus, "published", [])

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-mining"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-mining")
    return app


app = create_app()
