"""OIDC role wiring on sensitive write endpoints (services/_shared/auth.py).

Dev/test default is legacy passthrough (existing suites unaffected); these
tests inject a deterministic fixture verifier via ``app.state.auth_verifier``
to prove role enforcement (wrong role -> 403, right role passes).
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.main import create_app
from tests.helpers import BASE_LAT, BASE_LON, registration_body, square_geojson

OGUN = "/api/v1/states/ogun/cadastre"
TENANT_HDR = {"X-State-Tenant": "ogun"}


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


def _register(client: TestClient, uin: str = "OG-ABK-AUTH-1") -> dict:
    body = registration_body(uin, square_geojson(BASE_LON, BASE_LAT))
    resp = client.post(f"{OGUN}/parcels", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_dev_passthrough_titling_decision_still_works(client):
    """Without a verifier the legacy caller-supplied actor string passes."""
    parcel = _register(client)
    resp = client.post(
        f"{OGUN}/titling/{parcel['titling_workflow_id']}/decisions",
        json={"approved": True, "actor": "surveyor:amide"},
    )
    assert resp.status_code == 200, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_titling_decision_wrong_role_403(app, client):
    """The first pending stage (SURVEYOR_VALIDATION) requires 'surveyor' —
    a registry officer's token is rejected."""
    app.state.auth_verifier = _verifier("registry")
    parcel = _register(client)
    resp = client.post(
        f"{OGUN}/titling/{parcel['titling_workflow_id']}/decisions",
        json={"approved": True, "actor": "registry:amide"}, headers=_auth(),
    )
    assert resp.status_code == 403


def test_titling_decision_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("surveyor")
    parcel = _register(client)
    resp = client.post(
        f"{OGUN}/titling/{parcel['titling_workflow_id']}/decisions",
        json={"approved": True, "actor": "surveyor:amide"}, headers=_auth(),
    )
    assert resp.status_code == 200, resp.text


def test_consent_requires_governor_role(app, client):
    app.state.auth_verifier = _verifier("registry")
    parcel = _register(client)
    resp = client.post(
        f"{OGUN}/consents",
        json={"parcel_id": parcel["parcel_id"],
              "expires_at": "2027-01-01T00:00:00Z", "actor": "governor:x"},
        headers={**TENANT_HDR, **_auth()},
    )
    assert resp.status_code == 403


def test_encumbrance_register_requires_registry(app, client):
    app.state.auth_verifier = _verifier("auditor")
    parcel = _register(client)
    resp = client.post(
        f"{OGUN}/parcels/{parcel['parcel_id']}/encumbrances",
        json={"type": "CAVEAT", "instrument_hash": "h" * 16},
        headers={**TENANT_HDR, **_auth()},
    )
    assert resp.status_code == 403


def test_revocation_notice_requires_ministry(app, client):
    app.state.auth_verifier = _verifier("registry")
    parcel = _register(client)
    resp = client.post(
        f"{OGUN}/parcels/{parcel['parcel_id']}/revocations",
        json={"public_purpose": "road expansion", "actor": "ministry:lands"},
        headers={**TENANT_HDR, **_auth()},
    )
    assert resp.status_code == 403


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
