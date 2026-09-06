"""Journey 2 — control-plane tenant provisioning (Stage 7.B, in-process tier).

    create_tenant (local operators) -> per-step audit events
    -> rollback on an injected step failure -> idempotent resume
    -> tenant-isolation negative check across two tenants (via mod-kyc-kyb).
"""

from __future__ import annotations

import importlib
import json

from conftest import SyncASGIClient, build_control_plane, register_service_alias

FIRST_STATE = "ogun"
SECOND_STATE = "nasarawa"


def _create_tenant(client, state: str, tier: str = "hybrid") -> dict:
    resp = client.post(
        "/control/v1/tenants",
        json={"state": state, "tier": tier},
        headers={"authorization": "Bearer e2e-operator"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_tenant_provisioning_journey(control_plane, kyc_client, tmp_path):
    cp_client, store, archive_root = control_plane

    # ------------------------------------------------------------------
    # 1. create_tenant with the deterministic local operators.
    # ------------------------------------------------------------------
    op = _create_tenant(cp_client, FIRST_STATE)
    assert op["status"] == "active"
    tenant_id = op["tenant_id"]
    resources = op["provisioned_resources"]
    assert resources["k8s_namespace"] == f"sos-{FIRST_STATE}-hybrid"

    # ------------------------------------------------------------------
    # 2. Per-step audit events, in the fixed operator order.
    # ------------------------------------------------------------------
    resp = cp_client.get("/control/v1/audit-events")
    events = resp.json()["events"]
    step_events = [
        e for e in events
        if e["event_type"] == "ng.sos.tenant.provision_step"
        and e["tenant_id"] == tenant_id
        and e["detail"]["status"] == "completed"
    ]
    completed_steps = [e["detail"]["step"] for e in step_events]
    assert completed_steps == ["namespace", "postgres", "keycloak", "s3", "kms"]
    assert any(
        e["event_type"] == "ng.sos.tenant.provisioned" and e["tenant_id"] == tenant_id
        for e in events
    )
    # Re-posting the same state is idempotent: returns the existing tenant.
    again = _create_tenant(cp_client, FIRST_STATE)
    assert again["tenant_id"] == tenant_id

    # ------------------------------------------------------------------
    # 3. Rollback path: inject a one-shot failure into the `s3` step, then
    #    resume by re-posting (operator upsert semantics make retry safe).
    # ------------------------------------------------------------------
    root_alias = register_service_alias("cp_j2", "control-plane")
    operators_mod = importlib.import_module(f"{root_alias}.app.operators")
    local = operators_mod.local_operators()

    class FlakyS3Operator:
        """Fails the first provision attempt; converges on retry."""

        name = "s3"

        def __init__(self) -> None:
            self.attempts = 0
            self.decommissioned: list[str] = []

        def provision(self, tenant, resources) -> str:
            self.attempts += 1
            if self.attempts == 1:
                raise operators_mod.ProvisionError("injected s3 failure (e2e)")
            return "storage-provisioned"

        def decommission(self, tenant_id: str) -> None:
            self.decommissioned.append(tenant_id)

    flaky = FlakyS3Operator()
    local["s3"] = flaky

    app2, store2, archive_root2 = build_control_plane("cp_j2", tmp_path, operators=local)
    cp2 = SyncASGIClient(app2)

    op = _create_tenant(cp2, SECOND_STATE)
    failed_id = op["tenant_id"]
    assert op["status"] == "failed"

    # Rollback ran in reverse order over completed steps; all recorded.
    resp = cp2.get("/control/v1/audit-events")
    events2 = [e for e in resp.json()["events"] if e["tenant_id"] == failed_id]
    rollback_ops = [
        e["detail"]["operator"] for e in events2
        if e["event_type"] == "ng.sos.tenant.provision_rollback"
        and e["detail"]["status"] == "rolled-back"
    ]
    # namespace/postgres/keycloak completed before s3 failed; kms never ran.
    assert rollback_ops == ["keycloak", "postgres", "namespace"]
    assert any(e["event_type"] == "ng.sos.tenant.provision_failed" for e in events2)
    # No operator error text leaks raw detail beyond the sanitized message.
    assert "injected s3 failure" in json.dumps(events2)

    # 4. Idempotent resume: re-post the failed tenant; it converges to active
    #    with the same tenant_id and resources (upsert semantics).
    resumed = _create_tenant(cp2, SECOND_STATE)
    assert resumed["tenant_id"] == failed_id
    assert resumed["status"] == "active"
    assert flaky.attempts == 2

    resp = cp2.get("/control/v1/audit-events")
    events3 = [e for e in resp.json()["events"] if e["tenant_id"] == failed_id]
    completed = [
        e["detail"]["step"] for e in events3
        if e["event_type"] == "ng.sos.tenant.provision_step"
        and e["detail"]["status"] == "completed"
    ]
    # The resumed run re-executed all five steps to completion.
    assert completed[-5:] == ["namespace", "postgres", "keycloak", "s3", "kms"]

    # Both control-plane audit chains verify intact.
    import sosctl.audit as sosctl_audit

    assert sosctl_audit.verify_tenant_chain(archive_root, tenant_id) == []
    assert sosctl_audit.verify_tenant_chain(archive_root2, failed_id) == []

    # ------------------------------------------------------------------
    # 5. Tenant-isolation negative check: a KYC case created under one
    #    tenant must not be readable from another (403, not 404-leak).
    # ------------------------------------------------------------------
    resp = kyc_client.post(
        "/kyc/v1/cases",
        json={
            "state_id": FIRST_STATE,
            "subject_ref": "wallet-under-ogun",
            "actor": "e2e",
            "required_documents": [],
            "liveness_required": False,
        },
    )
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["case_id"]

    resp = kyc_client.get(f"/kyc/v1/cases/{case_id}", params={"state_id": FIRST_STATE})
    assert resp.status_code == 200
    resp = kyc_client.get(f"/kyc/v1/cases/{case_id}", params={"state_id": SECOND_STATE})
    assert resp.status_code == 403, resp.text
