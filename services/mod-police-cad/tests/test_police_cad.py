"""Tests for mod-police-cad — incl. both sides of the RatificationGate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.gate import RatificationGate
from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    """Default build: gate CLOSED (pre-ratification, the legal status quo)."""
    return TestClient(create_app())


@pytest.fixture()
def ratified_client() -> TestClient:
    """Post-ratification build (24-of-36 assemblies + assent recorded)."""
    return TestClient(create_app(gate=RatificationGate(ratified=True)))


def _unit(client: TestClient, state: str = "lagos") -> str:
    resp = client.post("/cad/v1/units", json={
        "tenant_state_id": state, "agency": "LNSC", "call_sign": "LNSC-ALPHA-1",
        "personnel_count": 12, "biometric_enrolled": True,
    })
    assert resp.status_code == 201
    return resp.json()["unit_id"]


# --- ratification-independent modules (always on) ----------------------------

def test_incident_intake_and_geofence(client: TestClient) -> None:
    ok = client.post("/cad/v1/incidents", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "category": "robbery",
        "latitude": 6.52, "longitude": 3.37,
    })
    assert ok.status_code == 201
    assert ok.json()["status"] == "open"

    outside = client.post("/cad/v1/incidents", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "category": "robbery",
        "latitude": 9.05, "longitude": 7.49,  # Abuja — outside Lagos geofence
    })
    assert outside.status_code == 422
    assert "geofence" in outside.json()["detail"]


def test_unit_registry_biometric_flag(client: TestClient) -> None:
    resp = client.post("/cad/v1/units", json={
        "tenant_state_id": "ogun", "agency": "SO-SAFE", "call_sign": "SOSAFE-7",
        "personnel_count": 40, "biometric_enrolled": False,
    })
    assert resp.status_code == 201
    assert resp.json()["biometric_enrolled"] is False


def test_geofenced_dispatch_event_log(client: TestClient) -> None:
    unit_id = _unit(client)
    inc = client.post("/cad/v1/incidents", json={
        "tenant_state_id": "lagos", "agency": "LNSC", "category": "medical",
        "latitude": 6.50, "longitude": 3.35,
    }).json()
    event = client.post("/cad/v1/dispatch", json={
        "incident_id": inc["incident_id"], "unit_id": unit_id,
        "latitude": 6.51, "longitude": 3.36,
    })
    assert event.status_code == 201
    assert event.json()["geofence_verified"] is True
    log = client.get("/cad/v1/dispatch-log").json()
    assert len(log) == 1

    # Dispatch outside the geofence is rejected and not logged.
    bad = client.post("/cad/v1/dispatch", json={
        "incident_id": inc["incident_id"], "unit_id": unit_id,
        "latitude": 8.0, "longitude": 8.0,
    })
    assert bad.status_code == 422
    assert len(client.get("/cad/v1/dispatch-log").json()) == 1

    # Cross-tenant dispatch prohibited.
    other_unit = _unit(client, state="osun")
    cross = client.post("/cad/v1/dispatch", json={
        "incident_id": inc["incident_id"], "unit_id": other_unit,
        "latitude": 6.51, "longitude": 3.36,
    })
    assert cross.status_code == 422


def test_trust_fund_public_audit_feed(client: TestClient) -> None:
    client.post("/cad/v1/trust-fund/donations", json={
        "tenant_state_id": "lagos", "donor_ref": "Dangote Industries", "amount_kobo": 500000000,
    })
    client.post("/cad/v1/trust-fund/donations", json={
        "tenant_state_id": "lagos", "donor_ref": "Lekki Estates Assoc.", "amount_kobo": 100000000,
    })
    disb = client.post("/cad/v1/trust-fund/disbursements", json={
        "tenant_state_id": "lagos", "purpose": "patrol vehicle O&M",
        "amount_kobo": 200000000,
    })
    assert disb.status_code == 201

    # Overspend rejected.
    over = client.post("/cad/v1/trust-fund/disbursements", json={
        "tenant_state_id": "lagos", "purpose": "excess", "amount_kobo": 999999999999,
    })
    assert over.status_code == 422

    feed = client.get("/cad/v1/trust-fund/lagos/audit-feed").json()
    assert feed["balance_kobo"] == 400000000
    assert len(feed["donations"]) == 2 and len(feed["disbursements"]) == 1


# --- ratification-GATED modules -----------------------------------------------

def test_gate_closed_returns_423_with_legal_basis(client: TestClient) -> None:
    assert client.get("/cad/v1/gate").json()["ratified"] is False

    arms = client.post("/cad/v1/arms-register", json={
        "tenant_state_id": "lagos", "serial_no": "AK-0001",
        "weapon_type": "rifle", "assigned_unit_id": _unit(client),
    })
    assert arms.status_code == 423
    detail = arms.json()["detail"]
    assert detail["error"] == "ratification_gated"
    assert "24 of 36" in detail["legal_basis"]
    assert "Ebubeagu" in detail["legal_basis"]

    standup = client.post("/cad/v1/state-force/stand-up", json={
        "tenant_state_id": "lagos", "force_name": "Lagos State Police Service",
        "enabling_law_ref": "LAGOS-SP-LAW-2027", "initial_strength": 5000,
    })
    assert standup.status_code == 423
    assert "presidential assent" in standup.json()["detail"]["legal_basis"]


def test_gate_open_enables_gated_endpoints(ratified_client: TestClient) -> None:
    assert ratified_client.get("/cad/v1/gate").json()["ratified"] is True
    unit_id = _unit(ratified_client)
    arms = ratified_client.post("/cad/v1/arms-register", json={
        "tenant_state_id": "lagos", "serial_no": "AK-0001",
        "weapon_type": "rifle", "assigned_unit_id": unit_id,
    })
    assert arms.status_code == 201
    assert arms.json()["serial_no"] == "AK-0001"

    standup = ratified_client.post("/cad/v1/state-force/stand-up", json={
        "tenant_state_id": "lagos", "force_name": "Lagos State Police Service",
        "enabling_law_ref": "LAGOS-SP-LAW-2027", "initial_strength": 5000,
    })
    assert standup.status_code == 201
    assert standup.json()["status"] == "pending_nass_certification"


def test_gate_default_is_closed() -> None:
    assert RatificationGate().ratified is False
