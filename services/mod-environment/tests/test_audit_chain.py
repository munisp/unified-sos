"""Hash-chained audit tests for mod-environment (app/audit.py + verify endpoint)."""
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
    e1 = log.record("environment.permit_created.created", "lagos", detail={"k": 1})
    e2 = log.record("environment.permit_created.updated", "lagos", detail={"k": 2})
    assert e1.prev_hash == "0" * 64
    assert e2.prev_hash == e1.event_hash
    assert log.verify() == []
    assert len(log.events(tenant_state_id="lagos")) == 2
    assert log.events(tenant_state_id="other") == []


def test_audit_tamper_detected() -> None:
    log = AuditLog()
    log.record("environment.permit_created.created", "lagos")
    log.record("environment.permit_created.updated", "lagos")
    victim = log._events[0]
    object.__setattr__(victim, "detail", {"tampered": True})
    assert log.verify() != []



def test_audit_verify_endpoint_after_mutation(client) -> None:
    resp = client.post("/environment/v1/permits", json={"tenant_state_id": "lagos", "permit_type": "EFFLUENT_DISCHARGE", "holder_id": "ACME-IND", "facility_id": "FAC-LG-001", "fee_kobo": 25000000})
    assert resp.status_code in (200, 201), resp.text
    verify = client.get("/api/v1/states/lagos/audit/verify")
    assert verify.status_code == 200
    body = verify.json()
    assert body["valid"] is True
    assert body["entries"] == 1



def test_audit_verify_endpoint_empty(client) -> None:
    resp = client.get("/api/v1/states/lagos/audit/verify")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "entries": 0}
