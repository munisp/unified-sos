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

JOURNAL = {
    "entry_id": "JE-AUTH-1",
    "date": "2026-01-15",
    "memo": "auth wiring test",
    "lines": [
        {"account_code": "1000", "debit_kobo": 1000},
        {"account_code": "3001", "credit_kobo": 1000},
    ],
    "source_event_id": "EVT-AUTH-1",
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


def test_dev_passthrough_journal_push_still_works(client):
    resp = client.post("/erp/v1/states/lagos/journals", json=JOURNAL)
    assert resp.status_code == 201, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_journal_push_wrong_role_403(app, client):
    app.state.auth_verifier = _verifier("auditor")
    resp = client.post("/erp/v1/states/lagos/journals", json=JOURNAL,
                       headers=_auth())
    assert resp.status_code == 403


def test_journal_push_right_role_passes(app, client):
    app.state.auth_verifier = _verifier("mda-officer")
    resp = client.post("/erp/v1/states/lagos/journals", json=JOURNAL,
                       headers=_auth())
    assert resp.status_code == 201, resp.text


def test_coa_mapping_put_requires_mda_officer(app, client):
    app.state.auth_verifier = _verifier("revenue-officer")
    resp = client.put("/erp/v1/states/lagos/coa-mapping",
                      json={"mapping": {"1000": "CASH"}}, headers=_auth())
    assert resp.status_code == 403


def test_journal_push_requires_bearer_when_verifier_set(app, client):
    app.state.auth_verifier = _verifier("mda-officer")
    resp = client.post("/erp/v1/states/lagos/journals", json=JOURNAL)
    assert resp.status_code == 401


def test_read_endpoints_open(client):
    assert client.get("/erp/v1/states/lagos/journals").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
