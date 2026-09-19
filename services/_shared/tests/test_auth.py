"""Shared OIDC JWT middleware (services/_shared/auth.py)."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth import (
    AuthConfigurationError,
    DEPRECATION_HEADER,
    assert_auth_bootable,
    require_role,
)


def _jwt_shape(payload: dict) -> str:
    """An unsigned JWT-shaped token (dev-mode decoding only)."""
    def _b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(payload)}.sig"


def _fixture_verifier(claims: dict):
    def verify(token: str, state):
        return claims

    return verify


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()

    @app.post("/governed")
    def governed(actor: str = Depends(require_role("governor"))):
        return {"actor": actor}

    return app


def test_dev_passthrough_without_token(app):
    resp = TestClient(app).post("/governed")
    assert resp.status_code == 200
    assert resp.headers[DEPRECATION_HEADER]
    assert resp.json()["actor"] == "governor:anonymous"


def test_dev_passthrough_with_legacy_actor_string(app):
    resp = TestClient(app).post(
        "/governed", headers={"Authorization": "Bearer governor:amide"})
    assert resp.status_code == 200
    assert resp.headers[DEPRECATION_HEADER]
    assert resp.json()["actor"] == "governor:governor:amide"


def test_dev_mode_jwt_shaped_token_passes(app):
    token = _jwt_shape({"sub": "officer-1", "role": "governor"})
    resp = TestClient(app).post(
        "/governed", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["actor"] == "governor:officer-1"


def test_fixture_verifier_right_role(app):
    app.state.auth_verifier = _fixture_verifier({"sub": "officer-1", "role": "governor"})
    resp = TestClient(app).post(
        "/governed", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 200
    assert resp.json()["actor"] == "governor:officer-1"
    assert DEPRECATION_HEADER not in resp.headers


def test_fixture_verifier_wrong_role_403(app):
    app.state.auth_verifier = _fixture_verifier({"sub": "officer-2", "role": "surveyor"})
    resp = TestClient(app).post(
        "/governed", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 403
    assert "governor" in resp.json()["detail"]


def test_fixture_verifier_requires_token(app):
    app.state.auth_verifier = _fixture_verifier({"sub": "x", "role": "governor"})
    assert TestClient(app).post("/governed").status_code == 401


def test_fixture_verifier_roles_list_claim(app):
    app.state.auth_verifier = _fixture_verifier({"sub": "x", "roles": ["governor"]})
    resp = TestClient(app).post(
        "/governed", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200


def test_production_without_jwks_fails_closed(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(AuthConfigurationError):
        assert_auth_bootable()


def test_production_request_without_token_rejected(app, monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.setenv("SOS_AUTH_JWKS_URL", "http://keycloak:8080/realms/sos-{state}/certs")
    assert TestClient(app).post("/governed").status_code == 401


def test_production_boot_ok_with_jwks(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.setenv("SOS_AUTH_JWKS_URL", "http://keycloak:8080/certs")
    assert_auth_bootable()  # no error
