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

TENANT = {"X-State-Tenant": "benue"}
FARMER = {"name": "Aondo Aba", "kyc_ref": "KYC-BEN-001", "lga": "Gboko"}


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


def test_dev_passthrough_farmer_registration_still_works(client):
    resp = client.post("/agri/v1/farmers", json=FARMER, headers=TENANT)
    assert resp.status_code == 201, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_farmer_registration_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("auditor")
    resp = client.post("/agri/v1/farmers", json=FARMER,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_farmer_registration_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("agent-supervisor")
    resp = client.post("/agri/v1/farmers", json=FARMER,
                       headers={**TENANT, **_auth()})
    assert resp.status_code == 201, resp.text


def test_receipt_issue_requires_agent_supervisor(app, client):
    app.state.auth_verifier = _verifier("registry")
    resp = client.post("/agri/v1/receipts", json={
        "warehouse_id": "WH-1", "lot_id": "LOT-1", "storage_fees_kobo": 1000,
    }, headers={**TENANT, **_auth()})
    assert resp.status_code == 403


def test_receipt_lifecycle_requires_bearer_when_verifier_set(app, client):
    app.state.auth_verifier = _verifier("agent-supervisor")
    resp = client.post("/agri/v1/receipts/WR-1/release", headers=TENANT)
    assert resp.status_code == 401


def test_read_endpoints_open(client):
    assert client.get("/agri/v1/farmers", headers=TENANT).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
