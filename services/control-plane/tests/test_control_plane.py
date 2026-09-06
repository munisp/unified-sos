"""Tests for the SOS control plane API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def valid_pack(state: str = "ogun") -> dict:
    return {
        "tenant_state_id": state,
        "policy_id": "POL_OGUN_REV_SPLIT_2026",
        "revenue_head": "REV_LUC",
        "effective_date": "2026-07-01",
        "gazette_reference": "OGSL-GAZETTE-2026-11",
        "statutory_split_rules": [
            {"beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND",
             "tigerbeetle_account_code": 3001, "split_percentage": 80.0,
             "deduction_timing": "INSTANT"},
            {"beneficiary": "MDA_RETENTION_ACCOUNT",
             "tigerbeetle_account_code": 2010, "split_percentage": 12.0,
             "deduction_timing": "INSTANT"},
        ],
        "concession_guardrails": {"revenue_share_ceiling_pct": 8.0},
    }


def _create(client: TestClient, state: str = "nasarawa", tier: str = "shared"):
    return client.post("/control/v1/tenants",
                       json={"state": state, "tier": tier, "realms": [f"sos-{state}"]})


def test_create_tenant_202_and_resources(client: TestClient) -> None:
    resp = _create(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["tenant_id"].startswith("tn-nasarawa-")
    # Reference provisioning completes synchronously: provisioning -> active.
    assert body["status"] == "active"
    res = body["provisioned_resources"]
    assert res["k8s_namespace"] == "sos-nasarawa-shared"
    assert res["postgres_schema"] == "tenant_nasarawa"
    assert res["keycloak_realm"] == "sos-nasarawa"
    assert res["s3_bucket"] == "sos-nasarawa-shared-data"
    assert res["kms_keyring"] == "sos-nasarawa-keyring"


def test_create_tenant_idempotent_by_state(client: TestClient) -> None:
    first = _create(client, "ogun", "hybrid").json()
    second = _create(client, "ogun", "hybrid").json()
    assert first["tenant_id"] == second["tenant_id"]


def test_create_tenant_rejects_bad_enums(client: TestClient) -> None:
    assert _create(client, "kano", "shared").status_code == 422  # schema enum
    assert _create(client, "ogun", "gold").status_code == 422


def test_workflow_states_recorded(client: TestClient) -> None:
    tid = _create(client).json()["tenant_id"]
    tenant = client.get(f"/control/v1/tenants/{tid}").json()
    assert tenant["status"] == "active"
    assert any("requested" in s for s in tenant["workflow"])
    assert any("active" in s for s in tenant["workflow"])


def test_suspend_lifecycle(client: TestClient) -> None:
    tid = _create(client, "taraba").json()["tenant_id"]
    resp = client.post(f"/control/v1/tenants/{tid}/suspend",
                       json={"reason": "concession breach"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"
    assert client.post(f"/control/v1/tenants/tn-nope/suspend",
                       json={"reason": "x"}).status_code == 404
    assert client.get("/control/v1/tenants/tn-nope").status_code == 404


def test_policy_pack_ingest_valid(client: TestClient) -> None:
    tid = _create(client, "ogun", "hybrid").json()["tenant_id"]
    resp = client.post(f"/control/v1/tenants/{tid}/policy-packs",
                       json={"policy_id": "POL_OGUN_REV_SPLIT_2026", "document": valid_pack()})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "ogun"
    packs = client.get(f"/control/v1/tenants/{tid}/policy-packs").json()
    assert len(packs) == 1 and packs[0]["policy_id"] == "POL_OGUN_REV_SPLIT_2026"


def test_policy_pack_ingest_guardrail_failure_422(client: TestClient) -> None:
    tid = _create(client, "ogun", "hybrid").json()["tenant_id"]
    doc = valid_pack()
    doc["statutory_split_rules"][1]["split_percentage"] = 50.0  # INSTANT sum 130
    resp = client.post(f"/control/v1/tenants/{tid}/policy-packs",
                       json={"policy_id": "POL_X", "document": doc})
    assert resp.status_code == 422
    assert "INSTANT" in str(resp.json()["detail"])
    # Nothing was registered.
    assert client.get(f"/control/v1/tenants/{tid}/policy-packs").json() == []


def test_policy_pack_unknown_tenant_404(client: TestClient) -> None:
    resp = client.post("/control/v1/tenants/tn-ghost/policy-packs",
                       json={"policy_id": "POL_X", "document": valid_pack()})
    assert resp.status_code == 404


def test_audit_log_append_only_and_ordered(client: TestClient) -> None:
    tid = _create(client).json()["tenant_id"]
    client.post(f"/control/v1/tenants/{tid}/suspend", json={"reason": "audit test"})
    feed = client.get("/control/v1/audit-events").json()
    events = feed["events"]
    assert feed["count"] == len(events) >= 2
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)  # monotonic, append-only
    types = [e["event_type"] for e in events]
    assert "ng.sos.tenant.provisioned" in types
    assert "ng.sos.tenant.suspended" in types
    # No mutation surface: only GET on audit-events.
    assert client.post("/control/v1/audit-events", json={}).status_code == 405


def test_pii_guard_rejects_pii_looking_fields(client: TestClient) -> None:
    resp = client.post("/control/v1/tenants", json={
        "state": "osun", "tier": "shared", "nin": "12345678901",
    })
    assert resp.status_code == 400
    assert "PII" in resp.json()["detail"]
    nested = client.post("/control/v1/tenants", json={
        "state": "osun", "tier": "shared",
        "meta": {"admin": {"first_name": "Ada", "last_name": "Obi"}},
    })
    assert nested.status_code == 400
    # Clean metadata passes.
    assert client.post("/control/v1/tenants",
                       json={"state": "osun", "tier": "shared"}).status_code == 202


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
