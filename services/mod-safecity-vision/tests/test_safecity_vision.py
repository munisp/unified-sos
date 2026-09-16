"""Tests for mod-safecity-vision — incl. both sides of the AuthorizationGate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _shared.eventbus import InMemoryEventBus
from _shared.hashchain import verify_event_chain
from app.adapters import (
    AdapterUnavailableError,
    FixtureFaceEngine,
    InsightFaceEngine,
    cosine_similarity,
    face_engine_from_env,
)
from app.domain import (
    EVENT_ANOMALY,
    EVENT_CROWD_ALERT,
    EVENT_FACE_MATCH,
    _point_in_polygon,
)
from app.gate import AuthorizationGate
from app.main import create_app

LAGOS = {"X-State-Tenant": "lagos"}
OGUN = {"X-State-Tenant": "ogun"}

CAMERA = {
    "name": "Marina Overlook 01",
    "latitude": 6.45,
    "longitude": 3.40,
    "stream_uri": "webrtc://edge.lagos/cam-001",
    "capabilities": ["face", "crowd", "anomaly"],
}

SQUARE = [(6.40, 3.35), (6.40, 3.45), (6.50, 3.45), (6.50, 3.35)]


def _camera(client: TestClient, headers=LAGOS, **overrides) -> str:
    body = {**CAMERA, **overrides}
    resp = client.post("/vision/v1/cameras", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["camera_id"]


# --- stream registry -----------------------------------------------------------

def test_register_camera_and_list(client: TestClient) -> None:
    cam_id = _camera(client)
    cams = client.get("/vision/v1/cameras", headers=LAGOS).json()
    assert len(cams) == 1
    assert cams[0]["camera_id"] == cam_id
    assert cams[0]["stream_uri"].startswith("webrtc://")


def test_register_camera_rejects_bad_scheme(client: TestClient) -> None:
    r = client.post("/vision/v1/cameras", json={**CAMERA, "stream_uri": "http://x"},
                    headers=LAGOS)
    assert r.status_code == 422
    assert "webrtc" in r.json()["detail"]


def test_rtsp_stream_accepted(client: TestClient) -> None:
    r = client.post("/vision/v1/cameras",
                    json={**CAMERA, "stream_uri": "rtsp://cam.lan/554"}, headers=LAGOS)
    assert r.status_code == 201


def test_missing_tenant_header_rejected(client: TestClient) -> None:
    assert client.get("/vision/v1/cameras").status_code == 400


# --- AuthorizationGate (locked side) -------------------------------------------

def test_gate_closed_returns_423_with_legal_basis(client: TestClient) -> None:
    assert client.get("/vision/v1/gate", headers=LAGOS).json()["biometric_authorized"] is False
    enroll = client.post("/vision/v1/faces/enroll", json={
        "subject_ref": "POI-001", "image_ref": "frame://POI-001",
    }, headers=LAGOS)
    assert enroll.status_code == 423
    detail = enroll.json()["detail"]
    assert detail["error"] == "biometric_authorization_gated"
    assert "Nigeria Data Protection Act 2023" in detail["legal_basis"]
    match = client.post("/vision/v1/faces/match", json={"image_ref": "frame://POI-001"},
                        headers=LAGOS)
    assert match.status_code == 423


def test_expired_authorization_is_locked(bus: InMemoryEventBus) -> None:
    from tests.conftest import _past, make_gate
    from app.gate import AuthorizationRecord
    gate = make_gate(AuthorizationRecord(ref="WRT-OLD", kind="warrant",
                                         tenant_state_id="lagos", expires_at=_past()))
    c = TestClient(create_app(gate=gate, bus=bus))
    r = c.post("/vision/v1/faces/enroll",
               json={"subject_ref": "POI-1", "image_ref": "frame://POI-1"}, headers=LAGOS)
    assert r.status_code == 423


def test_wrong_tenant_authorization_is_locked(authorized_client: TestClient) -> None:
    # Gate holds a warrant for lagos only; ogun remains locked (no cross-tenant authority).
    r = authorized_client.post("/vision/v1/faces/match",
                               json={"image_ref": "frame://POI-001"}, headers=OGUN)
    assert r.status_code == 423


def test_gate_from_env(authorizations_file: str) -> None:
    gate = AuthorizationGate.from_env({"SOS_VISION_AUTHORIZATIONS_FILE": authorizations_file})
    assert gate.authorized("ogun") is True
    assert gate.authorized("lagos") is False
    # Fail-closed: missing env / missing file -> closed everywhere.
    assert AuthorizationGate.from_env({}).authorized("ogun") is False
    assert AuthorizationGate.from_env(
        {"SOS_VISION_AUTHORIZATIONS_FILE": "/nonexistent.json"}).authorized("ogun") is False


# --- face recognition (unlocked side) -------------------------------------------

def _enroll(client: TestClient, subject: str = "POI-001") -> None:
    r = client.post("/vision/v1/faces/enroll",
                    json={"subject_ref": subject, "image_ref": f"frame://{subject}"},
                    headers=LAGOS)
    assert r.status_code == 201, r.text


def test_enroll_and_match_exact(authorized_client: TestClient) -> None:
    _enroll(authorized_client)
    r = authorized_client.post("/vision/v1/faces/match",
                               json={"image_ref": "frame://POI-001"}, headers=LAGOS)
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["subject_ref"] == "POI-001"
    assert body["similarity"] == pytest.approx(1.0)


def test_match_non_subject_below_threshold(authorized_client: TestClient) -> None:
    _enroll(authorized_client)
    r = authorized_client.post("/vision/v1/faces/match",
                               json={"image_ref": "frame://SOMEONE-ELSE"}, headers=LAGOS)
    body = r.json()
    assert body["matched"] is False
    assert body["subject_ref"] is None  # no biometric leak on non-match
    assert body["similarity"] < body["threshold"]


def test_match_threshold_is_honoured(authorized_client: TestClient) -> None:
    _enroll(authorized_client)
    # Identical probe passes even a strict threshold.
    strict = authorized_client.post("/vision/v1/faces/match", json={
        "image_ref": "frame://POI-001", "threshold": 0.99,
    }, headers=LAGOS).json()
    assert strict["matched"] is True
    # Non-subject fails the default threshold.
    default = authorized_client.post("/vision/v1/faces/match", json={
        "image_ref": "frame://OTHER",
    }, headers=LAGOS).json()
    assert default["matched"] is False


def test_match_publishes_event(authorized_client: TestClient, bus: InMemoryEventBus) -> None:
    _enroll(authorized_client)
    authorized_client.post("/vision/v1/faces/match",
                           json={"image_ref": "frame://POI-001"}, headers=LAGOS)
    topics = [e["topic"] for e in bus.published]
    assert topics == [EVENT_FACE_MATCH]
    assert bus.published[0]["payload"]["subject_ref"] == "POI-001"


def test_face_lookup_audit_hash_chain(authorized_client: TestClient) -> None:
    _enroll(authorized_client)
    for ref in ("frame://POI-001", "frame://OTHER", "frame://POI-001"):
        authorized_client.post("/vision/v1/faces/match", json={"image_ref": ref}, headers=LAGOS)
    feed = authorized_client.get("/vision/v1/faces/audit", headers=LAGOS).json()
    assert feed["entries"] == 3
    assert feed["chain_intact"] is True
    assert verify_event_chain(feed["records"]) == []
    # Tampering is detected.
    tampered = [dict(r) for r in feed["records"]]
    tampered[1]["similarity"] = 0.999
    assert verify_event_chain(tampered) != []
    # Every lookup carries the authorization ref under which it ran.
    assert all(r["authorization_ref"] == "WRT-LAG-2026-0142" for r in feed["records"])


# --- crowd monitoring (NOT gated) -----------------------------------------------

def test_crowd_observation_below_threshold_no_alert(client: TestClient,
                                                    bus: InMemoryEventBus) -> None:
    cam_id = _camera(client)
    r = client.post("/vision/v1/crowd/observations", json={
        "camera_id": cam_id, "persons": 12, "area_m2": 10.0, "flow_per_min": 4.0,
    }, headers=LAGOS)
    assert r.status_code == 201
    body = r.json()
    assert body["density"] == pytest.approx(1.2)
    assert body["alert"] is False
    assert bus.published == []


def test_crowd_alert_at_threshold_publishes_event(client: TestClient,
                                                  bus: InMemoryEventBus) -> None:
    cam_id = _camera(client)
    r = client.post("/vision/v1/crowd/observations", json={
        "camera_id": cam_id, "persons": 50, "area_m2": 10.0,
    }, headers=LAGOS)
    body = r.json()
    assert body["alert"] is True  # 5.0 p/m² >= 4.0 default — stampede risk
    assert bus.published[0]["topic"] == EVENT_CROWD_ALERT
    assert bus.published[0]["payload"]["density"] == pytest.approx(5.0)


def test_crowd_per_camera_threshold_configurable(client: TestClient) -> None:
    strict_cam = _camera(client, crowd_density_threshold=1.0)
    relaxed_cam = _camera(client, crowd_density_threshold=10.0)
    payload = {"persons": 20, "area_m2": 10.0}  # 2.0 p/m²
    strict = client.post("/vision/v1/crowd/observations",
                         json={"camera_id": strict_cam, **payload}, headers=LAGOS).json()
    relaxed = client.post("/vision/v1/crowd/observations",
                          json={"camera_id": relaxed_cam, **payload}, headers=LAGOS).json()
    assert strict["alert"] is True
    assert relaxed["alert"] is False


def test_crowd_unknown_camera_404(client: TestClient) -> None:
    r = client.post("/vision/v1/crowd/observations", json={
        "camera_id": "cam-nope", "persons": 1, "area_m2": 1.0,
    }, headers=LAGOS)
    assert r.status_code == 404


# --- anomaly detection (NOT gated) ----------------------------------------------

def test_loitering_rule(client: TestClient, bus: InMemoryEventBus) -> None:
    cam_id = _camera(client)
    short = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "loitering", "dwell_seconds": 60,
    }, headers=LAGOS).json()
    assert short["detected"] is False
    long = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "loitering", "dwell_seconds": 900,
    }, headers=LAGOS).json()
    assert long["detected"] is True
    assert bus.published[-1]["topic"] == EVENT_ANOMALY


def test_perimeter_breach_geofence_polygon(client: TestClient) -> None:
    cam_id = _camera(client)
    inside = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "perimeter_breach",
        "latitude": 6.45, "longitude": 3.40, "geofence_polygon": SQUARE,
    }, headers=LAGOS).json()
    assert inside["detected"] is True
    outside = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "perimeter_breach",
        "latitude": 6.60, "longitude": 3.50, "geofence_polygon": SQUARE,
    }, headers=LAGOS).json()
    assert outside["detected"] is False


def test_perimeter_breach_requires_polygon(client: TestClient) -> None:
    cam_id = _camera(client)
    r = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "perimeter_breach",
        "latitude": 6.45, "longitude": 3.40,
    }, headers=LAGOS)
    assert r.status_code == 422


def test_object_left_behind_rule(client: TestClient) -> None:
    cam_id = _camera(client)
    ok = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "object_left_behind", "unattended_seconds": 30,
    }, headers=LAGOS).json()
    assert ok["detected"] is False
    bad = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "object_left_behind", "unattended_seconds": 300,
    }, headers=LAGOS).json()
    assert bad["detected"] is True


def test_running_and_stampede_rules(client: TestClient) -> None:
    cam_id = _camera(client)
    walk = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "running", "speed_mps": 1.4,
    }, headers=LAGOS).json()
    run = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "running", "speed_mps": 5.5,
    }, headers=LAGOS).json()
    assert walk["detected"] is False and run["detected"] is True
    stampede = client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "stampede", "density": 6.0,
    }, headers=LAGOS).json()
    assert stampede["detected"] is True


def test_anomalies_listed_per_tenant(client: TestClient) -> None:
    cam_id = _camera(client)
    client.post("/vision/v1/anomalies", json={
        "camera_id": cam_id, "kind": "loitering", "dwell_seconds": 600,
    }, headers=LAGOS)
    assert len(client.get("/vision/v1/anomalies", headers=LAGOS).json()) == 1
    assert client.get("/vision/v1/anomalies", headers=OGUN).json() == []


# --- tenant isolation -------------------------------------------------------------

def test_tenant_isolation_cameras_and_watchlist(authorized_client: TestClient) -> None:
    lagos_cam = _camera(authorized_client, LAGOS)
    _enroll(authorized_client)
    # Ogun sees no Lagos cameras.
    assert authorized_client.get("/vision/v1/cameras", headers=OGUN).json() == []
    # Lagos camera id is not addressable from the Ogun tenant.
    r = authorized_client.post("/vision/v1/crowd/observations", json={
        "camera_id": lagos_cam, "persons": 100, "area_m2": 1.0,
    }, headers=OGUN)
    assert r.status_code == 404
    # Ogun match has an empty watchlist (and is gate-locked anyway).
    m = authorized_client.post("/vision/v1/faces/match",
                               json={"image_ref": "frame://POI-001"}, headers=OGUN)
    assert m.status_code == 423


# --- adapters / unit level ---------------------------------------------------------

def test_fixture_engine_deterministic_and_quantized() -> None:
    engine = FixtureFaceEngine()
    a = engine.embed("frame://X")
    assert a == engine.embed("frame://X")
    assert len(a) == 128
    assert all(-128 <= v <= 127 for v in a)
    assert cosine_similarity(a, a) == pytest.approx(1.0)
    assert abs(cosine_similarity(engine.embed("A"), engine.embed("B"))) < 0.5


def test_insightface_engine_fail_closed() -> None:
    with pytest.raises(AdapterUnavailableError, match="SOS_VISION_MODEL_DIR"):
        InsightFaceEngine(environ={})
    with pytest.raises(AdapterUnavailableError, match="unknown"):
        face_engine_from_env({"SOS_VISION_FACE_ENGINE": "bogus"})
    assert isinstance(face_engine_from_env({}), FixtureFaceEngine)


def test_point_in_polygon_unit() -> None:
    assert _point_in_polygon((6.45, 3.40), SQUARE) is True
    assert _point_in_polygon((7.0, 4.0), SQUARE) is False


# --- ops --------------------------------------------------------------------------

def test_healthz_and_metrics(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "http_requests_total" in metrics.text


# --- retention: enrolments bound to warrant expiry, matches capped -------------

from datetime import datetime as _dt, timedelta as _td, timezone as _tz  # noqa: E402

from app.domain import VisionStore  # noqa: E402


def _ts(dt: _dt) -> str:
    return dt.isoformat(timespec="seconds")


def test_enroll_binds_retention_to_warrant_expiry(authorized_client: TestClient) -> None:
    resp = authorized_client.post(
        "/vision/v1/faces/enroll",
        json={"subject_ref": "SUBJ-RET-1", "image_ref": "img-001"},
        headers={"X-State-Tenant": "lagos"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["retention_until"] is not None
    assert body["retention_until"] > body["enrolled_at"]


def test_sweep_purges_enrolment_and_embedding_at_expiry() -> None:
    store = VisionStore()
    expiry = _dt.now(_tz.utc) + _td(hours=1)
    enr = store.enroll_face(
        "lagos", "SUBJ-1", "img-001", "WRT-1", retention_until=_ts(expiry)
    )
    assert store._scope("lagos").enrolments[enr.enrolment_id].embedding
    counts = store.sweep_retention(now=expiry + _td(seconds=1))
    assert counts == {"lagos": {"enrolments": 1, "matches": 0}}
    assert enr.enrolment_id not in store._scope("lagos").enrolments
    assert enr.embedding == []  # biometric purged with the record


def test_sweep_appends_retention_purge_audit_event() -> None:
    store = VisionStore()
    expiry = _dt.now(_tz.utc) - _td(minutes=1)
    store.enroll_face("lagos", "SUBJ-2", "img-002", "WRT-2", retention_until=_ts(expiry))
    store.sweep_retention()
    feed = store.face_audit_feed("lagos")
    purges = [r for r in feed["records"] if r.get("event_type") == "RETENTION_PURGE"]
    assert len(purges) == 1
    assert purges[0]["authorization_ref"] == "WRT-2"
    assert feed["chain_intact"]


def test_no_purge_before_expiry() -> None:
    store = VisionStore()
    expiry = _dt.now(_tz.utc) + _td(days=1)
    enr = store.enroll_face(
        "lagos", "SUBJ-3", "img-003", "WRT-3", retention_until=_ts(expiry)
    )
    counts = store.sweep_retention(now=_dt.now(_tz.utc))
    assert counts == {}
    assert enr.enrolment_id in store._scope("lagos").enrolments
    assert store.face_audit_feed("lagos")["records"] == []


def test_match_records_purged_beyond_cap() -> None:
    store = VisionStore(match_retention_days=90)
    store.enroll_face("lagos", "SUBJ-4", "img-004", "WRT-4")
    recent = store.match_face("lagos", "img-004")
    old = store.match_face("lagos", "img-004")
    # Backdate one match beyond the 90-day cap.
    old.matched_at = _ts(_dt.now(_tz.utc) - _td(days=91))
    counts = store.sweep_retention()
    assert counts == {"lagos": {"enrolments": 0, "matches": 1}}
    remaining = store._scope("lagos").matches
    assert [m.match_event_id for m in remaining] == [recent.match_event_id]
    # Cap is injectable: a 30-day cap purges a 31-day-old match too.
    store2 = VisionStore(match_retention_days=30)
    store2.enroll_face("lagos", "SUBJ-5", "img-005", "WRT-5")
    m = store2.match_face("lagos", "img-005")
    m.matched_at = _ts(_dt.now(_tz.utc) - _td(days=31))
    assert store2.sweep_retention()["lagos"]["matches"] == 1
