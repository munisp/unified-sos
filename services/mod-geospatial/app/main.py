"""mod-geospatial FastAPI application.

Endpoints (all tenant-scoped by ``state_id`` path parameter):

* ``GET  /healthz``
* ``POST /api/v1/states/{state_id}/geospatial/datasets``
* ``GET  /api/v1/states/{state_id}/geospatial/datasets``
* ``GET  /api/v1/states/{state_id}/geospatial/datasets/{dataset_id}``
* ``POST /api/v1/states/{state_id}/geospatial/jobs``
* ``GET  /api/v1/states/{state_id}/geospatial/jobs``
* ``GET  /api/v1/states/{state_id}/geospatial/jobs/{job_id}``
* ``POST /api/v1/states/{state_id}/geospatial/jobs/{job_id}/run``
* ``POST /api/v1/states/{state_id}/geospatial/h3/index``
* ``POST /api/v1/states/{state_id}/geospatial/geolibre/projects``
* ``GET  /api/v1/states/{state_id}/geospatial/geolibre/projects/{project_id}``
* ``GET  /api/v1/states/{state_id}/geospatial/audit``

AuthN/Z: bearer JWT validated at the APISIX gateway (ADR-006/007); this
service trusts upstream-authenticated requests in local/test mode and never
mixes tenants — every repository call is explicitly state-scoped.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field

from .domain import (
    AdapterUnavailableError,
    DatasetType,
    JobTransitionError,
    JobType,
    NotFoundError,
    Sensitivity,
)
from .geometry import GeometryError
from .repository import InMemoryGeospatialRepository
from .service import GeospatialService
from .adapters.geolibre_adapter import RedactionError


class DatasetIn(BaseModel):
    dataset_type: DatasetType
    name: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=1)
    crs: str = "EPSG:4326"
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    h3_resolution: int = Field(default=9, ge=0, le=15)
    geometry: Optional[dict[str, Any]] = None
    bbox: Optional[list[float]] = None


class JobIn(BaseModel):
    job_type: JobType
    input_dataset_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class H3IndexIn(BaseModel):
    geometry: dict[str, Any]
    resolution: int = Field(default=9, ge=0, le=15)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dataset_ids: list[str] = Field(default_factory=list)
    source_job_id: Optional[str] = None


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


def create_app(service: Optional[GeospatialService] = None) -> FastAPI:
    app = FastAPI(title="mod-geospatial", version="0.1.0")
    app.state.service = service or GeospatialService(InMemoryGeospatialRepository())

    def get_service() -> GeospatialService:
        return app.state.service

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        svc = get_service()
        return {
            "status": "ok",
            "service": "mod-geospatial",
            "h3_backend": svc.h3.backend_name,
            "geolibre_backend": svc.geolibre_builder.backend_name,
        }

    base = "/api/v1/states/{state_id}/geospatial"

    @app.post(base + "/datasets", status_code=status.HTTP_201_CREATED)
    def register_dataset(state_id: str, body: DatasetIn, svc: GeospatialService = Depends(get_service)):
        try:
            return asdict(svc.register_dataset(state_id, body.model_dump()))
        except GeometryError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.get(base + "/datasets")
    def list_datasets(state_id: str, svc: GeospatialService = Depends(get_service)):
        return [asdict(d) for d in svc.repo.list_datasets(state_id)]

    @app.get(base + "/datasets/{dataset_id}")
    def get_dataset(state_id: str, dataset_id: str, svc: GeospatialService = Depends(get_service)):
        try:
            return asdict(svc.repo.get_dataset(state_id, dataset_id))
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found") from exc

    @app.post(base + "/jobs", status_code=status.HTTP_201_CREATED)
    def create_job(state_id: str, body: JobIn, svc: GeospatialService = Depends(get_service)):
        try:
            return asdict(svc.create_job(state_id, body.model_dump()))
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "input dataset not found") from exc

    @app.get(base + "/jobs")
    def list_jobs(state_id: str, svc: GeospatialService = Depends(get_service)):
        return [asdict(j) for j in svc.repo.list_jobs(state_id)]

    @app.get(base + "/jobs/{job_id}")
    def get_job(state_id: str, job_id: str, svc: GeospatialService = Depends(get_service)):
        try:
            return asdict(svc.repo.get_job(state_id, job_id))
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from exc

    @app.post(base + "/jobs/{job_id}/run")
    def run_job(state_id: str, job_id: str, svc: GeospatialService = Depends(get_service)):
        try:
            job = svc.run_job(state_id, job_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found") from exc
        except JobTransitionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return asdict(job)

    @app.post(base + "/h3/index")
    def h3_index(state_id: str, body: H3IndexIn, svc: GeospatialService = Depends(get_service)):
        try:
            return svc.h3_index(state_id, body.geometry, body.resolution)
        except GeometryError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.post(base + "/geolibre/projects", status_code=status.HTTP_201_CREATED)
    def build_project(state_id: str, body: ProjectIn, svc: GeospatialService = Depends(get_service)):
        try:
            return asdict(
                svc.build_project(
                    state_id, name=body.name, dataset_ids=body.dataset_ids, source_job_id=body.source_job_id
                )
            )
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found") from exc
        except RedactionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.get(base + "/geolibre/projects/{project_id}")
    def get_project(state_id: str, project_id: str, svc: GeospatialService = Depends(get_service)):
        try:
            return asdict(svc.repo.get_project(state_id, project_id))
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found") from exc

    @app.get(base + "/audit")
    def list_audit(state_id: str, svc: GeospatialService = Depends(get_service)):
        return [asdict(e) for e in svc.repo.list_audit(state_id)]

    @app.exception_handler(AdapterUnavailableError)
    def _adapter_unavailable(request, exc: AdapterUnavailableError):  # noqa: ANN001
        return Response(
            content=f'{{"detail": "{exc}"}}',
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-geospatial")
    return app


app = create_app()
