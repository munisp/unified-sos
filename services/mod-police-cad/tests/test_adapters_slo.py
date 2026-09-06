"""Tests for the WebRTC gateway binding, Wazuh SIEM binding, and the
dispatch-latency SLO instrumentation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import CadStore, DispatchLatencyTracker
from app.gate import RatificationGate
from app.main import create_app
from app.wazuh import FixtureWazuhAdapter, HttpWazuhAdapter, build_wazuh_adapter
from app.webrtc import (
    AdapterUnavailableError,
    FixtureWebRTCGateway,
    build_webrtc_gateway,
)

SDP_OFFER = "v=0\r\no=- 1 1 IN IP4 10.0.0.1\r\ns=cam\r\nt=0 0\r\nm=video 9 RTP/AVP 96\r\n"


@pytest.fixture()
def wazuh() -> FixtureWazuhAdapter:
    return FixtureWazuhAdapter()


@pytest.fixture()
def client(wazuh: FixtureWazuhAdapter) -> TestClient:
    return TestClient(create_app(wazuh=wazuh))


def _open_stream(client: TestClient, camera_id: str = "cam-lk-01",
                 tenant: str = "lagos", kind: str = "cctv") -> dict:
    resp = client.post(f"/cad/v1/streams/{camera_id}/session", json={
        "tenant_state_id": tenant, "kind": kind, "sdp_offer": SDP_OFFER,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- WebRTC gateway: fixture session lifecycle --------------------------------

def test_fixture_gateway_session_lifecycle(client: TestClient,
                                           wazuh: FixtureWazuhAdapter) -> None:
    session = _open_stream(client, kind="drone")
    assert session["camera_id"] == "cam-lk-01"
    assert session["kind"] == "drone"
    assert session["tenant_state_id"] == "lagos"
    assert session["started_at"]
    assert "m=video" in session["sdp_answer"]

    listed = client.get("/cad/v1/streams").json()
    assert [s["session_id"] for s in listed] == [session["session_id"]]

    opened = wazuh.by_type("stream_session_opened")
    assert len(opened) == 1 and opened[0]["camera_id"] == "cam-lk-01"

    closed = client.delete(f"/cad/v1/streams/{session['session_id']}")
    assert closed.status_code == 200
    assert closed.json()["closed"] is True
    assert client.get("/cad/v1/streams").json() == []
    assert len(wazuh.by_type("stream_session_closed")) == 1

    # Unknown session -> 404, no enumeration.
    assert client.delete(f"/cad/v1/streams/{session['session_id']}").status_code == 404


def test_stream_session_rejects_bad_kind_and_offer(client: TestClient) -> None:
    bad_kind = client.post("/cad/v1/streams/cam-1/session", json={
        "tenant_state_id": "lagos", "kind": "satellite", "sdp_offer": SDP_OFFER,
    })
    assert bad_kind.status_code == 422
    bad_offer = client.post("/cad/v1/streams/cam-1/session", json={
        "tenant_state_id": "lagos", "kind": "cctv", "sdp_offer": "not-sdp",
    })
    assert bad_offer.status_code == 422


def test_stream_listing_tenant_isolation(client: TestClient) -> None:
    lagos = _open_stream(client, camera_id="cam-lg", tenant="lagos")
    _open_stream(client, camera_id="cam-og", tenant="ogun")
    lagos_only = client.get("/cad/v1/streams", params={"tenant_state_id": "lagos"}).json()
    assert [s["session_id"] for s in lagos_only] == [lagos["session_id"]]
    assert len(client.get("/cad/v1/streams").json()) == 2


# --- WebRTC gateway: fail-closed production profile ----------------------------

def test_production_profile_without_url_fails_closed_at_boot() -> None:
    with pytest.raises(AdapterUnavailableError, match="SOS_WEBRTC_GATEWAY_URL"):
        build_webrtc_gateway({"SOS_CAD_PROFILE": "production"})


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(AdapterUnavailableError, match="SOS_CAD_PROFILE"):
        build_webrtc_gateway({"SOS_CAD_PROFILE": "staging"})
    with pytest.raises(AdapterUnavailableError):
        build_wazuh_adapter({"SOS_CAD_PROFILE": "staging"})


def test_fixture_gateway_direct() -> None:
    gw = FixtureWebRTCGateway()
    s = gw.create_stream_session("cam", "lagos", "cctv", SDP_OFFER)
    assert gw.list_sessions("lagos") == [s]
    assert gw.list_sessions("ogun") == []
    assert gw.close_session(s.session_id) is True
    assert gw.close_session(s.session_id) is False
    with pytest.raises(ValueError):
        gw.create_stream_session("cam", "lagos", "blimp", SDP_OFFER)


# --- Wazuh SIEM binding ---------------------------------------------------------

def test_wazuh_receives_dispatch_events(client: TestClient,
                                        wazuh: FixtureWazuhAdapter) -> None:
    unit = client.post("/cad/v1/units", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "call_sign": "LNSC-A",
        "personnel_count": 5, "biometric_enrolled": True,
    }).json()
    inc = client.post("/cad/v1/incidents", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "category": "robbery",
        "latitude": 6.52, "longitude": 3.37,
    }).json()
    resp = client.post("/cad/v1/dispatch", json={
        "incident_id": inc["incident_id"], "unit_id": unit["unit_id"],
        "latitude": 6.51, "longitude": 3.36,
    })
    assert resp.status_code == 201
    created = wazuh.by_type("incident_created")
    dispatched = wazuh.by_type("dispatch_created")
    assert len(created) == 1 and created[0]["tenant_state_id"] == "lagos"
    assert len(dispatched) == 1
    assert dispatched[0]["incident_id"] == inc["incident_id"]


def test_wazuh_receives_gate_denial(client: TestClient,
                                    wazuh: FixtureWazuhAdapter) -> None:
    resp = client.post("/cad/v1/state-force/stand-up", json={
        "tenant_state_id": "lagos", "force_name": "Lagos State Police Service",
        "enabling_law_ref": "LAGOS-SP-LAW-2027", "initial_strength": 100,
    })
    assert resp.status_code == 423
    denials = wazuh.by_type("gate_denial")
    assert len(denials) == 1
    assert denials[0]["category"] == "state-force-stand-up"
    assert denials[0]["tenant_state_id"] == "lagos"


def test_wazuh_receives_arms_register_attempt_and_denial(
        client: TestClient, wazuh: FixtureWazuhAdapter) -> None:
    resp = client.post("/cad/v1/arms-register", json={
        "tenant_state_id": "lagos", "serial_no": "AK-0001",
        "weapon_type": "rifle", "assigned_unit_id": "unit-x",
    })
    assert resp.status_code == 423
    assert len(wazuh.by_type("arms_register_attempt")) == 1
    denials = wazuh.by_type("gate_denial")
    assert len(denials) == 1 and denials[0]["category"] == "arms-register"


def test_http_wazuh_adapter_fails_closed_without_config() -> None:
    with pytest.raises(AdapterUnavailableError, match="SOS_WAZUH_URL"):
        build_wazuh_adapter({"SOS_CAD_PROFILE": "production"})
    with pytest.raises(AdapterUnavailableError, match="SOS_WAZUH_API_TOKEN"):
        build_wazuh_adapter({
            "SOS_CAD_PROFILE": "production",
            "SOS_WAZUH_URL": "https://wazuh.example",
        })
    adapter = build_wazuh_adapter({
        "SOS_CAD_PROFILE": "production",
        "SOS_WAZUH_URL": "https://wazuh.example",
        "SOS_WAZUH_API_TOKEN": "tok",
    })
    assert isinstance(adapter, HttpWazuhAdapter)


def test_default_profile_builds_fixtures() -> None:
    assert isinstance(build_webrtc_gateway({}), FixtureWebRTCGateway)
    assert isinstance(build_wazuh_adapter({}), FixtureWazuhAdapter)


# --- Dispatch-latency SLO --------------------------------------------------------

def test_latency_tracker_percentile_math() -> None:
    tracker = DispatchLatencyTracker()
    assert tracker.summary() == {
        "sample_count": 0, "p50_seconds": 0.0, "p95_seconds": 0.0,
        "slo_seconds": 30.0, "breach_count": 0, "within_slo": True,
    }
    for v in (10.0, 20.0, 31.5):
        tracker.record(v)
    s = tracker.summary()
    assert s["sample_count"] == 3
    assert s["p50_seconds"] == 20.0
    # linear interpolation: rank = 2 * 0.95 = 1.9 -> 20 + 0.9 * (31.5 - 20)
    assert s["p95_seconds"] == pytest.approx(30.35)
    assert s["breach_count"] == 1
    assert s["within_slo"] is False


def test_store_records_dispatch_latency() -> None:
    store = CadStore()
    unit = store.register_unit("lagos", "LNSC", "LNSC-A", 5, True)
    inc = store.intake_incident("lagos", "LNSC", "medical", 6.50, 3.35)
    store.dispatch(inc.incident_id, unit.unit_id, 6.51, 3.36)
    s = store.latency.summary()
    assert s["sample_count"] == 1
    assert 0.0 <= s["p95_seconds"] < 30.0
    assert s["breach_count"] == 0 and s["within_slo"] is True


def test_slo_endpoint_and_metrics_exposition(client: TestClient) -> None:
    unit = client.post("/cad/v1/units", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "call_sign": "LNSC-B",
        "personnel_count": 3, "biometric_enrolled": True,
    }).json()
    inc = client.post("/cad/v1/incidents", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "category": "fire",
        "latitude": 6.50, "longitude": 3.40,
    }).json()
    assert client.post("/cad/v1/dispatch", json={
        "incident_id": inc["incident_id"], "unit_id": unit["unit_id"],
        "latitude": 6.50, "longitude": 3.40,
    }).status_code == 201

    slo = client.get("/cad/v1/dispatch/slo").json()
    assert slo["sample_count"] == 1
    assert slo["slo_seconds"] == 30.0
    assert slo["breach_count"] == 0 and slo["within_slo"] is True

    metrics = client.get("/metrics").text
    assert "cad_dispatch_latency_p50_seconds" in metrics
    assert "cad_dispatch_latency_p95_seconds" in metrics
    assert "cad_dispatch_slo_breaches_total 0" in metrics
    assert "cad_dispatch_samples_total 1" in metrics
    # Shared http_* series still rendered by the shared registry.
    assert "http_requests_total" in metrics


def test_cross_tenant_dispatch_still_403_with_adapters(client: TestClient,
                                                       wazuh: FixtureWazuhAdapter) -> None:
    """Tenant isolation is preserved with the new bindings wired in."""
    inc = client.post("/cad/v1/incidents", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "category": "robbery",
        "latitude": 6.50, "longitude": 3.40,
    }).json()
    ogun_unit = client.post("/cad/v1/units", json={
        "tenant_state_id": "ogun", "agency": "SO-SAFE", "call_sign": "SS-1",
        "personnel_count": 2, "biometric_enrolled": True,
    }).json()
    r = client.post("/cad/v1/dispatch", json={
        "incident_id": inc["incident_id"], "unit_id": ogun_unit["unit_id"],
        "latitude": 6.50, "longitude": 3.40,
    })
    assert r.status_code == 403
    assert wazuh.by_type("dispatch_created") == []


def test_ratified_flow_still_works(wazuh: FixtureWazuhAdapter) -> None:
    client = TestClient(create_app(gate=RatificationGate(ratified=True), wazuh=wazuh))
    unit = client.post("/cad/v1/units", json={
        "tenant_state_id": "lagos", "agency": "NPF", "call_sign": "NPF-1",
        "personnel_count": 10, "biometric_enrolled": True,
    }).json()
    r = client.post("/cad/v1/arms-register", json={
        "tenant_state_id": "lagos", "serial_no": "AK-9",
        "weapon_type": "rifle", "assigned_unit_id": unit["unit_id"],
    })
    assert r.status_code == 201
    assert len(wazuh.by_type("arms_register_attempt")) == 1
    assert wazuh.by_type("gate_denial") == []
