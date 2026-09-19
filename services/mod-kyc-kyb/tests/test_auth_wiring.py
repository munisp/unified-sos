"""OIDC role wiring on the KYC/KYB review endpoints (_shared/auth.py).

Dev/test default is legacy passthrough (existing suites unaffected); a
deterministic fixture verifier is injected via ``app.state.auth_verifier``
to prove wrong role -> 403 and the right role ('kyc-reviewer') passes.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import create_app  # noqa: E402

TENANT = "lagos"


def _verifier(role: str):
    def verify(token: str, state):
        return {"sub": "officer-1", "role": role}

    return verify


def _case_in_review(client: TestClient) -> str:
    resp = client.post("/kyc/v1/cases", json={
        "state_id": TENANT, "subject_ref": "auth-subject",
        "subject_type": "CITIZEN_WALLET", "actor": "case-owner-1",
        "liveness_required": False,
    })
    assert resp.status_code == 201, resp.text
    cid = resp.json()["case_id"]
    resp = client.post(f"/kyc/v1/cases/{cid}/submit",
                       json={"state_id": TENANT, "actor": "case-owner-1"})
    assert resp.status_code == 200, resp.text
    return cid


def _review(client: TestClient, cid: str, **kwargs):
    return client.post(f"/kyc/v1/cases/{cid}/review", json={
        "state_id": TENANT, "decision": "APPROVE",
        "reviewer": "officer-1", "reason": "documents verified",
    }, **kwargs)


def test_dev_passthrough_review_still_works():
    client = TestClient(create_app())
    cid = _case_in_review(client)
    resp = _review(client, cid)
    assert resp.status_code == 200, resp.text
    assert "x-auth-deprecation" in {k.lower() for k in resp.headers}


def test_review_wrong_role_403():
    app = create_app()
    app.state.auth_verifier = _verifier("surveyor")
    client = TestClient(app)
    cid = _case_in_review(client)
    resp = _review(client, cid, headers={"Authorization": "Bearer t"})
    assert resp.status_code == 403


def test_review_right_role_passes():
    app = create_app()
    app.state.auth_verifier = _verifier("kyc-reviewer")
    client = TestClient(app)
    cid = _case_in_review(client)
    resp = _review(client, cid, headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200, resp.text


def test_production_profile_without_jwks_fails_boot(monkeypatch):
    monkeypatch.setenv("SOS_AUTH_PROFILE", "production")
    monkeypatch.delenv("SOS_AUTH_JWKS_URL", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
