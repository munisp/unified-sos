"""OIDC role wiring on tenant-lifecycle mutations (services/_shared/auth.py).

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


def test_dev_passthrough_tenant_create_still_works(client):
    resp = client.post("/control/v1/tenants",
                       json={"state": "nasarawa", "tier": "shared"})
    assert resp.status_code == 202, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_suspend_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("ministry")
    resp = client.post("/control/v1/tenants/t-missing/suspend",
                       json={"reason": "maintenance"}, headers=_auth())
    assert resp.status_code == 403


def test_suspend_right_role_reaches_handler(app, client):
    app.state.auth_verifier = _verifier("governor")
    resp = client.post("/control/v1/tenants/t-missing/suspend",
                       json={"reason": "maintenance"}, headers=_auth())
    assert resp.status_code == 404  # auth passed; tenant does not exist


def test_tenant_create_requires_governor(app, client):
    app.state.auth_verifier = _verifier("registry")
    resp = client.post("/control/v1/tenants",
                       json={"state": "ogun", "tier": "shared"}, headers=_auth())
    assert resp.status_code == 403


def test_policy_pack_ingest_requires_ministry(app, client):
    app.state.auth_verifier = _verifier("governor")
    resp = client.post("/control/v1/tenants/t-missing/policy-packs",
                       json={"ref": "x", "document": {}}, headers=_auth())
    assert resp.status_code == 403


def test_read_endpoints_open(client):
    assert client.get("/control/v1/tenants").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
