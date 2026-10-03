"""OIDC role wiring on mutating endpoints (services/_shared/auth.py).

Dev/test default is legacy passthrough (existing suites unaffected); these
tests inject a deterministic fixture verifier via ``app.state.auth_verifier``
to prove role enforcement (wrong role -> 403, right role passes).
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import create_app

TENANT = {"X-State-Tenant": "lagos"}
DREDGER = {
    "vessel_name": "MV Dredge One",
    "license_no": "NIWA-2026-001",
    "operator_kyb_ref": "KYB-123",
    "monthly_quota_m3": 500.0,
}
TRIP = {
    "route_id": "lagos-lagoon-commuter",
    "vessel": "MV Lagoon Queen",
    "capacity": 50,
    "departure": "2026-06-01T08:00:00Z",
}


def _verifier(role: str, sub: str = "officer-1"):
    def verify(token: str, state):
        return {"sub": sub, "role": role}

    return verify


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


def _auth() -> dict:
    return {"Authorization": "Bearer fixture-token"}


def test_dev_passthrough_dredger_registration_still_works(client):
    resp = client.post("/waterways/v1/dredgers", json=DREDGER, headers=TENANT)
    assert resp.status_code == 201, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_dredger_registration_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("surveyor")
    resp = client.post("/waterways/v1/dredgers", json=DREDGER,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_dredger_registration_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("registry")
    resp = client.post("/waterways/v1/dredgers", json=DREDGER,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 201, resp.text


def test_trip_schedule_requires_dispatch(app, client):
    app.state.auth_verifier = _verifier("registry")
    resp = client.post("/waterways/v1/trips", json=TRIP,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_read_endpoints_open(client):
    assert client.get("/waterways/v1/routes", headers=TENANT).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
