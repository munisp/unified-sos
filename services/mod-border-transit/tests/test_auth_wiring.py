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
CROSSING = {
    "name": "Seme Border",
    "neighbor_country": "BJ",
    "latitude": 6.38,
    "longitude": 2.72,
}
POLICY = {"flat_fee_kobo": 50000, "ad_valorem_bps": 150}


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


def test_dev_passthrough_crossing_create_still_works(client):
    resp = client.post("/border/v1/crossings/seme", json=CROSSING, headers=TENANT)
    assert resp.status_code == 201, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_crossing_create_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("revenue-officer")
    resp = client.post("/border/v1/crossings/seme", json=CROSSING,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_crossing_create_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("registry")
    resp = client.post("/border/v1/crossings/seme", json=CROSSING,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 201, resp.text


def test_levy_policy_requires_revenue_officer(app, client):
    app.state.auth_verifier = _verifier("mda-officer")
    resp = client.put("/border/v1/levy/policy", json=POLICY,
                      headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_levy_policy_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("revenue-officer")
    resp = client.put("/border/v1/levy/policy", json=POLICY,
                      headers={**TENANT, **_auth()})
    assert resp.status_code == 200, resp.text


def test_consignment_clear_requires_mda_officer(app, client):
    app.state.auth_verifier = _verifier("registry")
    resp = client.post("/border/v1/consignments/C-1/clear",
                       json={"officer_ref": "officer-1"},
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_read_endpoints_open(client):
    assert client.get("/border/v1/crossings", headers=TENANT).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
