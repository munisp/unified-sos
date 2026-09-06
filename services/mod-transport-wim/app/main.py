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

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-transport-wim")
    return app


app = create_app()
