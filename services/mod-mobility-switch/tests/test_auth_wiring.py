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

FARE_TABLE = {
    "gazette_reference": "LAMATA-HARMONIZATION-2026-03",
    "union_commission_pct": 5.0,
    "fares": [{"mode": "bus", "route": "BRT-IKORODU-CMS", "fare_kobo": 50000}],
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


def test_dev_passthrough_fare_table_still_works(client):
    resp = client.put("/mobility/v1/fares/lagos", json=FARE_TABLE)
    assert resp.status_code == 200, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_fare_table_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("auditor")
    resp = client.put("/mobility/v1/fares/lagos", json=FARE_TABLE, headers=_auth())
    assert resp.status_code == 403


def test_fare_table_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("revenue-officer")
    resp = client.put("/mobility/v1/fares/lagos", json=FARE_TABLE, headers=_auth())
    assert resp.status_code == 200, resp.text


def test_settlement_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("dispatch")
    resp = client.post("/mobility/v1/settlements/lagos/OP-1", headers=_auth())
    assert resp.status_code == 403


def test_escrow_prepare_requires_bearer_when_verifier_set(app, client):
    app.state.auth_verifier = _verifier("revenue-officer")
    resp = client.post("/mobility/v1/escrow", json={
        "transfer_id": "TX-1", "batch_id": "B-1", "amount_kobo": 1000,
    })
    assert resp.status_code == 401


def test_nibss_webhook_not_role_gated(client):
    """Signature-authenticated webhook must NOT require a role: it fails
    closed at the adapter seam (503), not at role enforcement."""
    resp = client.post("/mobility/v1/webhooks/nibss/ebills", json={
        "bill_reference": "BILL-1", "amount_kobo": 1000,
    })
    assert resp.status_code == 503  # adapter unconfigured, not 401/403


def test_healthz_open(client):
    assert client.get("/healthz").status_code == 200


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
