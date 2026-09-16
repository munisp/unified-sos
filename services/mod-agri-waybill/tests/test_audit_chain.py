"""Hash-chained audit tests for mod-agri-waybill (app/audit.py + verify endpoint)."""
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
    e1 = log.record("waybill.issued.created", "benue", detail={"k": 1})
    e2 = log.record("waybill.issued.updated", "benue", detail={"k": 2})
    assert e1.prev_hash == "0" * 64
    assert e2.prev_hash == e1.event_hash
    assert log.verify() == []
    assert len(log.events(tenant_state_id="benue")) == 2
    assert log.events(tenant_state_id="other") == []


def test_audit_tamper_detected() -> None:
    log = AuditLog()
    log.record("waybill.issued.created", "benue")
    log.record("waybill.issued.updated", "benue")
    victim = log._events[0]
    object.__setattr__(victim, "detail", {"tampered": True})
    assert log.verify() != []



def test_audit_verify_endpoint_after_mutation(client) -> None:
    resp = client.post("/waybills", json={"waybill_number": "WB-BEN-2026-000123", "state_id": "benue", "consignor_id": "FARM-COOP-GBOKO-12", "consignee_id": "MKT-LAG-MILE12", "produce_type": "YAM_TUBERS", "quantity_kg": 8200.0, "vehicle_plate": "BEN-221-ZX", "origin": "GBOKO", "destination": "LAGOS_MILE12", "levy_kobo": 250000})
    assert resp.status_code in (200, 201), resp.text
    verify = client.get("/api/v1/states/benue/audit/verify")
    assert verify.status_code == 200
    body = verify.json()
    assert body["valid"] is True
    assert body["entries"] == 1



def test_audit_verify_endpoint_empty(client) -> None:
    resp = client.get("/api/v1/states/benue/audit/verify")
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "entries": 0}
