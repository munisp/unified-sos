"""mod-mining FastAPI application."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

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


def create_app(
    repo: Optional[MiningRepository] = None, bus: Optional[EventBus] = None
) -> FastAPI:
    app = FastAPI(title="mod-mining — Solid Minerals Custody & Levies")
    app.state.repo = repo or InMemoryMiningRepository()
    app.state.bus = bus or InMemoryEventBus()

    def service(request: Request) -> MiningService:
        return MiningService(request.app.state.repo, request.app.state.bus)

    @app.post("/sites", response_model=MineralSite, status_code=201)
    def register_site(site: MineralSite, svc: MiningService = Depends(service)):
        return svc.register_site(site)

    @app.get("/sites/{site_id}", response_model=MineralSite)
    def get_site(site_id: str, request: Request):
        site = request.app.state.repo.get_site(site_id)
        if site is None:
            raise HTTPException(404, f"site {site_id!r} not found")
        return site

    @app.post("/consignments", response_model=Consignment, status_code=201)
    def create_consignment(
        consignment: Consignment, svc: MiningService = Depends(service)
    ):
        try:
            return svc.create_consignment(consignment)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

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

    return app


app = create_app()
