"""Hash-chained audit tests for mod-mining (app/audit.py + verify endpoint)."""
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
    e1 = log.record("mining.site_registered.created", "nasarawa", detail={"k": 1})
    e2 = log.record("mining.site_registered.updated", "nasarawa", detail={"k": 2})
    assert e1.prev_hash == "0" * 64
    assert e2.prev_hash == e1.event_hash
    assert log.verify() == []
    assert len(log.events(tenant_state_id="nasarawa")) == 2
    assert log.events(tenant_state_id="other") == []


def test_audit_tamper_detected() -> None:
    log = AuditLog()
    log.record("mining.site_registered.created", "nasarawa")
    log.record("mining.site_registered.updated", "nasarawa")
    victim = log._events[0]
    object.__setattr__(victim, "detail", {"tampered": True})
    assert log.verify() != []



def test_audit_verify_endpoint_after_mutation(client) -> None:
    resp = client.post("/sites", json={"site_id": "SITE-NAS-KOKO-01", "state_id": "nasarawa", "mine_lease_id": "ML-NAS-KOKO-04", "operator_name": "Koko Lithium Ltd", "minerals": ["LITHIUM_SPODUMENE"]})
    assert resp.status_code in (200, 201), resp.text
    verify = client.get("/api/v1/states/nasarawa/audit/verify")
    assert verify.status_code == 200
    body = verify.json()
    assert body["valid"] is True
    assert body["entries"] == 1



def test_audit_verify_endpoint_empty(client) -> None:
    resp = client.get("/api/v1/states/nasarawa/audit/verify")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "entries": 0}
