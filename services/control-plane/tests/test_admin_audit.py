"""Admin-token hardening: timing-safe compare + ADMIN_ACCESS audit entries."""
from __future__ import annotations

import hashlib
import inspect

from fastapi.testclient import TestClient

import app.main as main_mod
from app.branding import BrandingRegistry
from app.main import create_app

ADMIN = "correct-horse-token"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("SOS_CP_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("SOS_PROFILE", "production")
    return TestClient(create_app(branding=BrandingRegistry()))


from tests.test_branding import valid_branding  # noqa: E402


def _put(client: TestClient, token: str | None):
    headers = {"X-Admin-Token": token} if token else {}
    return client.put(
        "/cp/v1/tenants/lagos/branding",
        json=valid_branding(),
        headers=headers,
    )


def _admin_events(client: TestClient) -> list[dict]:
    return [
        e for e in client.get("/control/v1/audit-events").json()["events"]
        if e["event_type"] == "ng.sos.admin.access"
    ]


def test_correct_token_passes_and_audits(monkeypatch) -> None:
    client = _client(monkeypatch)
    resp = _put(client, ADMIN)
    assert resp.status_code == 200
    events = _admin_events(client)
    assert len(events) == 1
    detail = events[0]["detail"]
    assert detail["outcome"] == "allowed"
    assert detail["endpoint"] == "/cp/v1/tenants/lagos/branding"
    # Only the token hash is recorded — never the raw token.
    assert detail["token_sha256"] == hashlib.sha256(ADMIN.encode()).hexdigest()
    assert ADMIN not in str(events)


def test_wrong_token_403_and_audited(monkeypatch) -> None:
    client = _client(monkeypatch)
    resp = _put(client, "wrong-token")
    assert resp.status_code == 403
    events = _admin_events(client)
    assert len(events) == 1
    detail = events[0]["detail"]
    assert detail["outcome"] == "denied_bad_token"
    assert detail["token_sha256"] == hashlib.sha256(b"wrong-token").hexdigest()
    assert "wrong-token" not in str(events)


def test_missing_token_403_and_audited(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert _put(client, None).status_code == 403
    assert _admin_events(client)[0]["detail"]["outcome"] == "denied_bad_token"


def test_timing_safe_compare_used() -> None:
    src = inspect.getsource(main_mod.require_admin)
    assert "hmac.compare_digest" in src
    assert "presented != expected" not in src and "==" not in src.split("compare_digest")[0].split("presented")[1]


def test_actor_not_raw_token(monkeypatch) -> None:
    """The verified actor handle must not expose the raw token."""
    client = _client(monkeypatch)
    assert _put(client, ADMIN).status_code == 200
    branding_events = [
        e for e in client.get("/control/v1/audit-events").json()["events"]
    ]
    assert ADMIN not in str(branding_events)
