"""FastAPI surface for mod-safecity-vision — Safe-City AI/ML/DL/CV analytics.

Crowd-monitoring and anomaly-detection endpoints are always available.
Face-recognition enroll/match endpoints are locked behind the
AuthorizationGate (NDPA 2023 — biometric processing requires a certified
lawful basis) and return HTTP 423 until a valid warrant/DPO authorization
record is loaded from the certified source.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .adapters import AdapterUnavailableError, face_engine_from_env
from .domain import (
    AnomalyDetectedEvent,
    AnomalyEvent,
    AnomalyKind,
    Camera,
    CrowdAlertEvent,
    CrowdObservation,
    FaceEnrolment,
    FaceMatchEvent,
    FaceMatchResult,
    VisionStore,
    EVENT_ANOMALY,
    EVENT_CROWD_ALERT,
    EVENT_FACE_MATCH,
)
from .gate import AuthorizationGate, GatedError

# --- shared event bus (services/_shared/eventbus) -----------------------------
try:
    from _shared.eventbus import EventBus, InMemoryEventBus, event_bus_from_env
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.eventbus import EventBus, InMemoryEventBus, event_bus_from_env
    except ImportError:  # minimal container images ship only the app package
        EventBus = None  # type: ignore[assignment]
        InMemoryEventBus = None  # type: ignore[assignment]
        event_bus_from_env = None  # type: ignore[assignment]

# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys2
    from pathlib import Path as _Path2

    _services_root2 = _Path2(__file__).resolve().parents[2]
    if str(_services_root2) not in _sys2.path:
        _sys2.path.insert(0, str(_services_root2))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None


def get_store(request: Request) -> VisionStore:
    return request.app.state.store


def get_gate(request: Request) -> AuthorizationGate:
    return request.app.state.gate


def get_bus(request: Request):
    return request.app.state.bus


def tenant_from_header(x_state_tenant: str | None = Header(default=None)) -> str:
    """All state is scoped by the ``X-State-Tenant`` header (multi-tenancy)."""
    if not x_state_tenant:
        raise HTTPException(status_code=400, detail="X-State-Tenant header is required")
    return x_state_tenant.lower()


def _require_authorized(gate: AuthorizationGate, tenant: str) -> str:
    """Return the valid authorization ref or raise HTTP 423 with legal basis."""
    try:
        gate.check(tenant)
    except GatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": "biometric_authorization_gated",
                "legal_basis": exc.legal_basis,
                "gate": "SOS_VISION_AUTHORIZATIONS_FILE",
            },
        )
    record = next(r for r in gate.records if r.valid(tenant))
    return record.ref


class CameraRegistration(BaseModel):
    name: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    stream_uri: str = Field(..., examples=["webrtc://edge.lagos/cam-001"])
    capabilities: list[str] = Field(default_factory=list)
    crowd_density_threshold: float = Field(default=4.0, gt=0)


class FaceEnrollRequest(BaseModel):
    """biometric_authorization_gated: enroll a watchlist subject."""

    subject_ref: str
    image_ref: str


class FaceMatchRequest(BaseModel):
    """biometric_authorization_gated: match a probe image on the watchlist."""

    image_ref: str
    camera_id: str | None = None
    threshold: float = Field(default=0.65, ge=0.0, le=1.0)


class CrowdObservationRequest(BaseModel):
    camera_id: str
    persons: int = Field(..., ge=0)
    area_m2: float = Field(..., gt=0)
    flow_per_min: float = Field(default=0.0, ge=0)


class AnomalyRequest(BaseModel):
    camera_id: str
    kind: AnomalyKind
    dwell_seconds: float | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    geofence_polygon: list[tuple[float, float]] | None = None
    unattended_seconds: float | None = None
    speed_mps: float | None = None
    density: float | None = None


def _bus_from_env_safe():
    if event_bus_from_env is None:
        return None
    try:
        return event_bus_from_env()
    except Exception:
        # fail-soft for the reference build: fall back to in-memory and keep
        # serving; production wiring sets EVENT_BUS explicitly.
        return InMemoryEventBus()


def create_app(
    store: VisionStore | None = None,
    gate: AuthorizationGate | None = None,
    bus=None,
) -> FastAPI:
    app = FastAPI(
        title="SOS mod-safecity-vision — Safe-City AI/ML/DL/CV Analytics",
        version="0.1.0",
        description="CCTV/drone estate analytics: NDPA-gated face recognition, "
                    "crowd density & stampede alerts, anomaly detection.",
    )
    if store is None:
        try:
            store = VisionStore(engine=face_engine_from_env())
        except AdapterUnavailableError:
            raise
    app.state.store = store
    app.state.gate = gate or AuthorizationGate.from_env()
    app.state.bus = bus if bus is not None else _bus_from_env_safe()

    def _publish(topic: str, payload: BaseModel) -> None:
        if app.state.bus is not None:
            app.state.bus.publish(topic, payload)

    # --- stream registry (not gated) -----------------------------------------
    @app.post("/vision/v1/cameras", status_code=status.HTTP_201_CREATED,
              response_model=Camera, tags=["registry"])
    def register_camera(req: CameraRegistration,
                        tenant: str = Depends(tenant_from_header),
                        store: VisionStore = Depends(get_store)):
        try:
            return store.register_camera(tenant, req.name, req.latitude, req.longitude,
                                         req.stream_uri, req.capabilities,
                                         req.crowd_density_threshold)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/vision/v1/cameras", response_model=list[Camera], tags=["registry"])
    def list_cameras(tenant: str = Depends(tenant_from_header),
                     store: VisionStore = Depends(get_store)):
        return store.list_cameras(tenant)

    # --- face recognition (AuthorizationGate-gated; NDPA 2023) ----------------
    @app.post("/vision/v1/faces/enroll", status_code=status.HTTP_201_CREATED,
              response_model=FaceEnrolment, tags=["biometric_gated"])
    def enroll_face(req: FaceEnrollRequest,
                    tenant: str = Depends(tenant_from_header),
                    store: VisionStore = Depends(get_store),
                    gate: AuthorizationGate = Depends(get_gate)):
        auth_ref = _require_authorized(gate, tenant)
        return store.enroll_face(tenant, req.subject_ref, req.image_ref, auth_ref)

    @app.post("/vision/v1/faces/match", response_model=FaceMatchResult,
              tags=["biometric_gated"])
    def match_face(req: FaceMatchRequest,
                   tenant: str = Depends(tenant_from_header),
                   store: VisionStore = Depends(get_store),
                   gate: AuthorizationGate = Depends(get_gate)):
        auth_ref = _require_authorized(gate, tenant)
        try:
            result = store.match_face(tenant, req.image_ref, req.camera_id,
                                      req.threshold, authorization_ref=auth_ref)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        if result.matched:
            _publish(EVENT_FACE_MATCH, FaceMatchEvent(
                tenant_state_id=tenant,
                match_event_id=result.match_event_id,
                camera_id=result.camera_id,
                subject_ref=result.subject_ref or "",
                similarity=result.similarity,
                matched_at=result.matched_at,
            ))
        return result

    @app.get("/vision/v1/faces/audit", tags=["biometric_gated"])
    def face_audit(tenant: str = Depends(tenant_from_header),
                   store: VisionStore = Depends(get_store),
                   gate: AuthorizationGate = Depends(get_gate)):
        """Hash-chained audit feed of face lookups. Gated (biometric registry)."""
        _require_authorized(gate, tenant)
        return store.face_audit_feed(tenant)

    # --- crowd monitoring (NOT gated) -----------------------------------------
    @app.post("/vision/v1/crowd/observations", status_code=status.HTTP_201_CREATED,
              response_model=CrowdObservation, tags=["crowd"])
    def observe_crowd(req: CrowdObservationRequest,
                      tenant: str = Depends(tenant_from_header),
                      store: VisionStore = Depends(get_store)):
        try:
            obs = store.observe_crowd(tenant, req.camera_id, req.persons,
                                      req.area_m2, req.flow_per_min)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        if obs.alert:
            _publish(EVENT_CROWD_ALERT, CrowdAlertEvent(
                tenant_state_id=tenant, camera_id=obs.camera_id,
                persons=obs.persons, density=obs.density,
                threshold=obs.threshold, observed_at=obs.observed_at,
            ))
        return obs

    # --- anomaly detection (NOT gated) ----------------------------------------
    @app.post("/vision/v1/anomalies", status_code=status.HTTP_201_CREATED,
              response_model=AnomalyEvent, tags=["anomaly"])
    def detect_anomaly(req: AnomalyRequest,
                       tenant: str = Depends(tenant_from_header),
                       store: VisionStore = Depends(get_store)):
        try:
            event = store.detect_anomaly(
                tenant, req.camera_id, req.kind,
                dwell_seconds=req.dwell_seconds,
                latitude=req.latitude, longitude=req.longitude,
                geofence_polygon=req.geofence_polygon,
                unattended_seconds=req.unattended_seconds,
                speed_mps=req.speed_mps, density=req.density,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if event.detected:
            _publish(EVENT_ANOMALY, AnomalyDetectedEvent(
                tenant_state_id=tenant, camera_id=event.camera_id,
                kind=event.kind, detail=event.detail, detected_at=event.detected_at,
            ))
        return event

    @app.get("/vision/v1/anomalies", response_model=list[AnomalyEvent], tags=["anomaly"])
    def list_anomalies(tenant: str = Depends(tenant_from_header),
                       store: VisionStore = Depends(get_store)):
        return store.list_anomalies(tenant)

    @app.get("/vision/v1/gate")
    def gate_status(tenant: str = Depends(tenant_from_header),
                    gate: AuthorizationGate = Depends(get_gate)):
        return {
            "tenant_state_id": tenant,
            "biometric_authorized": gate.authorized(tenant),
            "gated_categories": ["faces-enroll", "faces-match", "faces-audit"],
            "legal_basis": gate.legal_basis,
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-safecity-vision")
    return app


app = create_app()
