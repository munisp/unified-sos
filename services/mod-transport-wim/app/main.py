"""mod-transport-wim FastAPI application."""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request

from .models import (
    ANPREvent,
    CorridorConfig,
    EManifest,
    FineAssessment,
    ManifestVerification,
    OverloadVerdict,
    WIMReading,
)
from .service import NotFoundError, WIMService


def create_app(service: Optional[WIMService] = None) -> FastAPI:
    app = FastAPI(title="mod-transport-wim — Weigh-in-Motion & Corridor Haulage")
    app.state.service = service or WIMService()

    def svc(request: Request) -> WIMService:
        return request.app.state.service

    @app.post("/corridors", response_model=CorridorConfig, status_code=201)
    def configure_corridor(config: CorridorConfig, request: Request):
        return svc(request).configure_corridor(config)

    @app.get("/corridors/{corridor_id}", response_model=CorridorConfig)
    def get_corridor(corridor_id: str, request: Request):
        try:
            return svc(request).get_corridor(corridor_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/wim/readings", response_model=OverloadVerdict, status_code=201)
    def ingest_reading(reading: WIMReading, request: Request):
        """Ingest a WIM reading; overloads trigger automatic fine assessment."""
        try:
            return svc(request).ingest_reading(reading)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/wim/readings/{reading_id}/verdict", response_model=OverloadVerdict)
    def get_verdict(reading_id: str, request: Request):
        try:
            return svc(request).get_verdict(reading_id)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/anpr/events", response_model=ANPREvent, status_code=201)
    def ingest_anpr(event: ANPREvent, request: Request):
        try:
            return svc(request).ingest_anpr(event)
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))

    @app.get("/fines", response_model=List[FineAssessment])
    def list_fines(request: Request, vehicle_plate: Optional[str] = None):
        return svc(request).list_fines(vehicle_plate)

    @app.post("/manifests", response_model=EManifest, status_code=201)
    def register_manifest(manifest: EManifest, request: Request):
        return svc(request).register_manifest(manifest)

    @app.get("/manifests/{manifest_id}/verify", response_model=ManifestVerification)
    def verify_manifest(manifest_id: str, request: Request):
        """Checkpoint e-manifest verification — SLO < 5 s (in-process ≈ ms)."""
        return svc(request).verify_manifest(manifest_id)

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-transport-wim"}

    return app


app = create_app()
