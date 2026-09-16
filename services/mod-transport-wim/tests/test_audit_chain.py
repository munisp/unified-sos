"""Hash-chained audit tests for mod-transport-wim (app/audit.py + verify endpoint)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_audit_chain_valid() -> None:
    log = AuditLog()
    e1 = log.record("wim.corridor_configured.created", "ogun", detail={"k": 1})
    e2 = log.record("wim.corridor_configured.updated", "ogun", detail={"k": 2})
    assert e1.prev_hash == "0" * 64
    assert e2.prev_hash == e1.event_hash
    assert log.verify() == []
    assert len(log.events(tenant_state_id="ogun")) == 2
    assert log.events(tenant_state_id="other") == []


def test_audit_tamper_detected() -> None:
    log = AuditLog()
    log.record("wim.corridor_configured.created", "ogun")
    log.record("wim.corridor_configured.updated", "ogun")
    victim = log._events[0]
    object.__setattr__(victim, "detail", {"tampered": True})
    assert log.verify() != []



def test_audit_verify_endpoint_after_mutation(client) -> None:
    resp = client.post("/corridors", json={"corridor_id": "COR-OGN-SAGAMU-EWK", "state_id": "ogun", "single_axle_limit_kg": 10000.0, "tandem_axle_limit_kg": 18000.0, "gvw_limit_kg": 48000.0, "fine_base_kobo": 5000000, "fine_per_overload_kg_kobo": 1000, "tolerance_pct": 0.0})
    assert resp.status_code in (200, 201), resp.text
    verify = client.get("/api/v1/states/ogun/audit/verify")
    assert verify.status_code == 200
    body = verify.json()
    assert body["valid"] is True
    assert body["entries"] == 1



def test_audit_verify_endpoint_empty(client) -> None:
    resp = client.get("/api/v1/states/ogun/audit/verify")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "entries": 0}
