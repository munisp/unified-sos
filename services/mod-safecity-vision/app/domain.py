"""Domain: Safe-City vision analytics — cameras, faces, crowd, anomalies.

All state is tenant-scoped (``X-State-Tenant``). Deterministic fixture
engines run by default; production models live behind the fail-closed
adapters in ``adapters.py``. Biometric lookups are appended to a
hash-chained audit log (services/_shared/hashchain, P1 audit immutability).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from .adapters import Embedding, FaceEngineAdapter, FixtureFaceEngine, cosine_similarity

# --- hash-chained audit: reuse services/_shared/hashchain, local fallback ----
try:
    from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash, verify_event_chain
except ImportError:  # minimal container images ship only the app package
    import hashlib
    import json

    GENESIS_PREV_HASH = "0" * 64

    def event_payload_hash(payload, prev_hash):  # type: ignore[no-redef]
        body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
        body["prev_hash"] = prev_hash
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def verify_event_chain(events):  # type: ignore[no-redef]
        errors: list[str] = []
        last = None
        for i, event in enumerate(events):
            prev = event.get("prev_hash")
            expected = GENESIS_PREV_HASH if last is None else last
            if prev != expected:
                errors.append(f"event {i}: broken chain link")
            if event.get("event_hash") != event_payload_hash(event, prev or ""):
                errors.append(f"event {i}: hash mismatch — record tampered")
            last = event.get("event_hash")
        return errors


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class CrossTenantError(ValueError):
    """Tenant-isolation violation — mapped to HTTP 403 at the API layer
    (fail-closed, matching mod-police-cad / mod-ppp-investment)."""


#: Shared event names (AsyncAPI contract intent; orchestrator wires topics).
EVENT_FACE_MATCH = "ng.sos.safecity.face_match"
EVENT_CROWD_ALERT = "ng.sos.safecity.crowd_alert"
EVENT_ANOMALY = "ng.sos.safecity.anomaly_detected"

DEFAULT_MATCH_THRESHOLD = 0.65
DEFAULT_CROWD_DENSITY_THRESHOLD = 4.0  # persons/m² — stampede-risk band
DEFAULT_LOITER_SECONDS = 300.0
DEFAULT_UNATTENDED_SECONDS = 120.0
DEFAULT_RUNNING_SPEED_MPS = 3.0


class StreamProtocol(str, Enum):
    WEBRTC = "webrtc"
    RTSP = "rtsp"


class Camera(BaseModel):
    camera_id: str
    tenant_state_id: str
    name: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    stream_uri: str  # webrtc:// or rtsp://
    capabilities: list[str] = Field(
        default_factory=list,
        description="e.g. ['face', 'crowd', 'anomaly']",
    )
    crowd_density_threshold: float = Field(
        default=DEFAULT_CROWD_DENSITY_THRESHOLD,
        gt=0,
        description="persons/m² above which a crowd alert is raised",
    )
    registered_at: str


class FaceEnrolment(BaseModel):
    enrolment_id: str
    tenant_state_id: str
    subject_ref: str  # watchlist subject reference (pseudonymized)
    embedding: list[int]  # 128-d int8-quantized
    authorization_ref: str  # warrant / DPO approval ref under which enrolled
    enrolled_at: str


class FaceMatchResult(BaseModel):
    match_event_id: str
    tenant_state_id: str
    camera_id: str | None = None
    matched: bool
    subject_ref: str | None = None
    similarity: float
    threshold: float
    matched_at: str


class CrowdObservation(BaseModel):
    observation_id: str
    tenant_state_id: str
    camera_id: str
    persons: int = Field(..., ge=0)
    area_m2: float = Field(..., gt=0)
    flow_per_min: float = Field(default=0.0, ge=0)
    density: float  # persons/m²
    alert: bool
    threshold: float
    observed_at: str


class AnomalyKind(str, Enum):
    LOITERING = "loitering"
    PERIMETER_BREACH = "perimeter_breach"
    OBJECT_LEFT_BEHIND = "object_left_behind"
    RUNNING = "running"
    STAMPEDE = "stampede"


class AnomalyEvent(BaseModel):
    event_id: str
    tenant_state_id: str
    camera_id: str
    kind: AnomalyKind
    detected: bool
    detail: str
    detected_at: str


# --- events published on the shared bus -------------------------------------

class FaceMatchEvent(BaseModel):
    tenant_state_id: str
    match_event_id: str
    camera_id: str | None
    subject_ref: str
    similarity: float
    matched_at: str


class CrowdAlertEvent(BaseModel):
    tenant_state_id: str
    camera_id: str
    persons: int
    density: float
    threshold: float
    observed_at: str


class AnomalyDetectedEvent(BaseModel):
    tenant_state_id: str
    camera_id: str
    kind: AnomalyKind
    detail: str
    detected_at: str


class _TenantScope:
    def __init__(self) -> None:
        self.cameras: dict[str, Camera] = {}
        self.enrolments: dict[str, FaceEnrolment] = {}
        self.matches: list[FaceMatchResult] = []
        self.observations: list[CrowdObservation] = []
        self.anomalies: list[AnomalyEvent] = []
        self.face_audit: list[dict] = []


class VisionStore:
    """In-memory, tenant-scoped vision analytics store (reference build)."""

    def __init__(self, engine: FaceEngineAdapter | None = None) -> None:
        self.engine = engine or FixtureFaceEngine()
        self._lock = threading.Lock()
        self._scopes: dict[str, _TenantScope] = {}

    # -- tenant plumbing ------------------------------------------------------
    def _scope(self, tenant: str) -> _TenantScope:
        with self._lock:
            if tenant not in self._scopes:
                self._scopes[tenant] = _TenantScope()
            return self._scopes[tenant]

    def _camera(self, tenant: str, camera_id: str) -> Camera:
        cam = self._scope(tenant).cameras.get(camera_id)
        if cam is None:
            raise KeyError(f"camera '{camera_id}' not found")
        return cam

    # -- stream registry ------------------------------------------------------
    def register_camera(
        self,
        tenant: str,
        name: str,
        latitude: float,
        longitude: float,
        stream_uri: str,
        capabilities: list[str] | None = None,
        crowd_density_threshold: float = DEFAULT_CROWD_DENSITY_THRESHOLD,
    ) -> Camera:
        scheme = stream_uri.split("://", 1)[0]
        if scheme not in {p.value for p in StreamProtocol}:
            raise ValueError(
                f"stream_uri scheme must be webrtc:// or rtsp:// (got {scheme!r})"
            )
        camera = Camera(
            camera_id=_id("cam"),
            tenant_state_id=tenant,
            name=name,
            latitude=latitude,
            longitude=longitude,
            stream_uri=stream_uri,
            capabilities=capabilities or [],
            crowd_density_threshold=crowd_density_threshold,
            registered_at=_now(),
        )
        self._scope(tenant).cameras[camera.camera_id] = camera
        return camera

    def list_cameras(self, tenant: str) -> list[Camera]:
        return list(self._scope(tenant).cameras.values())

    # -- face recognition (AuthorizationGate-gated at the API layer) ----------
    def enroll_face(
        self, tenant: str, subject_ref: str, image_ref: str, authorization_ref: str
    ) -> FaceEnrolment:
        enrolment = FaceEnrolment(
            enrolment_id=_id("enr"),
            tenant_state_id=tenant,
            subject_ref=subject_ref,
            embedding=list(self.engine.embed(image_ref)),
            authorization_ref=authorization_ref,
            enrolled_at=_now(),
        )
        self._scope(tenant).enrolments[enrolment.enrolment_id] = enrolment
        return enrolment

    def match_face(
        self,
        tenant: str,
        image_ref: str,
        camera_id: str | None = None,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        authorization_ref: str | None = None,
    ) -> FaceMatchResult:
        """Match a probe image against the tenant watchlist; audit the lookup."""
        scope = self._scope(tenant)
        if camera_id is not None:
            self._camera(tenant, camera_id)
        probe = self.engine.embed(image_ref)
        best_ref: str | None = None
        best_sim = 0.0
        for enr in scope.enrolments.values():
            sim = cosine_similarity(probe, enr.embedding)  # type: ignore[arg-type]
            if sim > best_sim:
                best_sim, best_ref = sim, enr.subject_ref
        matched = best_ref is not None and best_sim >= threshold
        result = FaceMatchResult(
            match_event_id=_id("match"),
            tenant_state_id=tenant,
            camera_id=camera_id,
            matched=matched,
            subject_ref=best_ref if matched else None,
            similarity=round(best_sim, 6),
            threshold=threshold,
            matched_at=_now(),
        )
        scope.matches.append(result)
        self._audit_lookup(scope, result, authorization_ref)
        return result

    def _audit_lookup(
        self, scope: _TenantScope, result: FaceMatchResult, authorization_ref: str | None
    ) -> None:
        """Append the lookup to the hash-chained audit log (tamper-evident)."""
        with self._lock:
            prev = scope.face_audit[-1]["event_hash"] if scope.face_audit else GENESIS_PREV_HASH
            record = {
                "event_id": _id("aud"),
                "match_event_id": result.match_event_id,
                "tenant_state_id": result.tenant_state_id,
                "camera_id": result.camera_id,
                "matched": result.matched,
                "subject_ref": result.subject_ref,
                "similarity": result.similarity,
                "threshold": result.threshold,
                "authorization_ref": authorization_ref,
                "audited_at": result.matched_at,
                "prev_hash": prev,
            }
            record["event_hash"] = event_payload_hash(record, prev)
            scope.face_audit.append(record)

    def face_audit_feed(self, tenant: str) -> dict:
        scope = self._scope(tenant)
        return {
            "tenant_state_id": tenant,
            "entries": len(scope.face_audit),
            "chain_intact": not verify_event_chain(scope.face_audit),
            "chain_errors": verify_event_chain(scope.face_audit),
            "records": list(scope.face_audit),
        }

    # -- crowd monitoring / density estimation --------------------------------
    def observe_crowd(
        self,
        tenant: str,
        camera_id: str,
        persons: int,
        area_m2: float,
        flow_per_min: float = 0.0,
    ) -> CrowdObservation:
        camera = self._camera(tenant, camera_id)
        density = persons / area_m2
        alert = density >= camera.crowd_density_threshold
        observation = CrowdObservation(
            observation_id=_id("obs"),
            tenant_state_id=tenant,
            camera_id=camera_id,
            persons=persons,
            area_m2=area_m2,
            flow_per_min=flow_per_min,
            density=round(density, 4),
            alert=alert,
            threshold=camera.crowd_density_threshold,
            observed_at=_now(),
        )
        self._scope(tenant).observations.append(observation)
        return observation

    # -- anomaly detection (deterministic fixture rules by default) -----------
    def detect_anomaly(
        self,
        tenant: str,
        camera_id: str,
        kind: AnomalyKind,
        *,
        dwell_seconds: float | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        geofence_polygon: list[tuple[float, float]] | None = None,
        unattended_seconds: float | None = None,
        speed_mps: float | None = None,
        density: float | None = None,
    ) -> AnomalyEvent:
        self._camera(tenant, camera_id)
        detected, detail = _apply_rule(
            kind,
            dwell_seconds=dwell_seconds,
            point=(latitude, longitude) if latitude is not None and longitude is not None else None,
            geofence_polygon=geofence_polygon,
            unattended_seconds=unattended_seconds,
            speed_mps=speed_mps,
            density=density,
        )
        event = AnomalyEvent(
            event_id=_id("anom"),
            tenant_state_id=tenant,
            camera_id=camera_id,
            kind=kind,
            detected=detected,
            detail=detail,
            detected_at=_now(),
        )
        self._scope(tenant).anomalies.append(event)
        return event

    def list_anomalies(self, tenant: str) -> list[AnomalyEvent]:
        return list(self._scope(tenant).anomalies)


# --- fixture rules -------------------------------------------------------------

def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon (lat/lon planar approximation)."""
    lat, lon = point
    inside = False
    n = len(polygon)
    for i in range(n):
        y1, x1 = polygon[i]
        y2, x2 = polygon[(i + 1) % n]
        if (x1 > lon) != (x2 > lon):
            at_lat = (y2 - y1) * (lon - x1) / (x2 - x1) + y1
            if lat < at_lat:
                inside = not inside
    return inside


def _apply_rule(kind: AnomalyKind, **kw) -> tuple[bool, str]:
    if kind is AnomalyKind.LOITERING:
        dwell = kw.get("dwell_seconds") or 0.0
        detected = dwell >= DEFAULT_LOITER_SECONDS
        return detected, f"dwell {dwell:.0f}s vs threshold {DEFAULT_LOITER_SECONDS:.0f}s"
    if kind is AnomalyKind.PERIMETER_BREACH:
        point, polygon = kw.get("point"), kw.get("geofence_polygon")
        if point is None or not polygon or len(polygon) < 3:
            raise ValueError("perimeter_breach requires a point and a >=3-vertex polygon")
        detected = _point_in_polygon(point, polygon)
        return detected, f"point {point} {'inside' if detected else 'outside'} geofence"
    if kind is AnomalyKind.OBJECT_LEFT_BEHIND:
        unattended = kw.get("unattended_seconds") or 0.0
        detected = unattended >= DEFAULT_UNATTENDED_SECONDS
        return detected, (
            f"unattended {unattended:.0f}s vs threshold {DEFAULT_UNATTENDED_SECONDS:.0f}s"
        )
    if kind is AnomalyKind.RUNNING:
        speed = kw.get("speed_mps") or 0.0
        detected = speed >= DEFAULT_RUNNING_SPEED_MPS
        return detected, f"speed {speed:.2f} m/s vs threshold {DEFAULT_RUNNING_SPEED_MPS:.2f} m/s"
    if kind is AnomalyKind.STAMPEDE:
        density = kw.get("density") or 0.0
        detected = density >= DEFAULT_CROWD_DENSITY_THRESHOLD
        return detected, (
            f"density {density:.2f} p/m² vs threshold {DEFAULT_CROWD_DENSITY_THRESHOLD:.2f} p/m²"
        )
    raise ValueError(f"unsupported anomaly kind {kind!r}")
