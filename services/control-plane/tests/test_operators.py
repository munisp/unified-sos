"""Tests for the provisioning operators and orchestration workflow."""

from __future__ import annotations

import pytest

from app.domain import MetadataStore, TenantCreate, TenantStatus
from app.operators import OPERATOR_ORDER, local_operators
from app.provisioning import ProvisioningWorkflowError, run_provisioning_workflow


class FakeOperator:
    """Recording fake operator; fails at provision when configured."""

    def __init__(self, name: str, calls: list[str], fail: bool = False,
                 fail_message: str = "boom") -> None:
        self.name = name
        self.calls = calls
        self.fail = fail
        self.fail_message = fail_message
        self.provisioned: list[str] = []
        self.decommissioned: list[str] = []

    def provision(self, tenant, resources) -> str:
        if self.fail:
            raise RuntimeError(self.fail_message)
        self.provisioned.append(tenant.tenant_id)
        self.calls.append(f"provision:{self.name}")
        return f"{self.name}-done"

    def decommission(self, tenant_id: str) -> None:
        self.decommissioned.append(tenant_id)
        self.calls.append(f"decommission:{self.name}")


def _fake_operators(fail_at: str | None = None, fail_message: str = "boom",
                    calls: list[str] | None = None) -> dict:
    calls = calls if calls is not None else []
    return {
        name: FakeOperator(name, calls, fail=(name == fail_at),
                           fail_message=fail_message)
        for name in OPERATOR_ORDER
    }


def _make_tenant(state: str = "ogun"):
    store = MetadataStore(operators=local_operators())
    tenant = store.create_tenant(TenantCreate(state=state, tier="hybrid"))
    assert tenant.status == TenantStatus.ACTIVE
    return tenant


def test_workflow_runs_steps_in_fixed_order() -> None:
    tenant = _make_tenant()
    operators = _fake_operators()
    run_provisioning_workflow(tenant, operators)
    for op in operators.values():
        assert op.provisioned == [tenant.tenant_id]
    markers = [entry.split(" ", 1)[-1] for entry in tenant.workflow]
    assert markers[-6:-1] == [f"{name}-done" for name in OPERATOR_ORDER]
    assert markers[-1] == "active"


@pytest.mark.parametrize("fail_at", list(OPERATOR_ORDER))
def test_failure_rolls_back_completed_steps_in_reverse(fail_at: str) -> None:
    tenant = _make_tenant(state={"namespace": "lagos", "postgres": "ogun",
                                 "keycloak": "osun", "s3": "benue",
                                 "kms": "taraba"}[fail_at])
    operators = _fake_operators(fail_at=fail_at)
    events: list[tuple[str, dict]] = []
    with pytest.raises(ProvisioningWorkflowError):
        run_provisioning_workflow(
            tenant, operators,
            emit=lambda t, tid, d: events.append((t, d)),
        )
    assert tenant.status == TenantStatus.FAILED

    idx = OPERATOR_ORDER.index(fail_at)
    expected_rollback = list(reversed(OPERATOR_ORDER[:idx]))
    rollback_calls = [
        c.removeprefix("decommission:") for c in operators["namespace"].calls
        if c.startswith("decommission:")
    ]
    # Every completed step is rolled back, in reverse provisioning order.
    assert rollback_calls == expected_rollback
    # The failing operator itself is never rolled back.
    assert operators[fail_at].decommissioned == []

    step_events = [d for t, d in events if t == "ng.sos.tenant.provision_step"]
    started = [d["step"] for d in step_events if d["status"] == "started"]
    assert started == list(OPERATOR_ORDER[: idx + 1])
    failed = [d for d in step_events if d["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["step"] == fail_at
    rollback_events = [d for t, d in events if t == "ng.sos.tenant.provision_rollback"]
    assert [d["operator"] for d in rollback_events] == expected_rollback


def test_store_marks_tenant_failed_and_audits() -> None:
    store = MetadataStore(operators=_fake_operators(fail_at="keycloak"))
    tenant = store.create_tenant(TenantCreate(state="nasarawa", tier="shared"))
    assert tenant.status == TenantStatus.FAILED
    types = [e.event_type for e in store.audit_events()]
    assert "ng.sos.tenant.provisioning_requested" in types
    assert "ng.sos.tenant.provision_step" in types
    assert "ng.sos.tenant.provision_rollback" in types
    assert "ng.sos.tenant.provision_failed" in types
    assert "ng.sos.tenant.provisioned" not in types


def test_failed_tenant_retry_resumes_and_succeeds() -> None:
    operators = _fake_operators(fail_at="s3")
    store = MetadataStore(operators=operators)
    failed = store.create_tenant(TenantCreate(state="taraba", tier="dedicated"))
    assert failed.status == TenantStatus.FAILED
    # Fix the failing operator; re-posting the same state resumes (upsert).
    operators["s3"].fail = False
    retried = store.create_tenant(TenantCreate(state="taraba", tier="dedicated"))
    assert retried.tenant_id == failed.tenant_id
    assert retried.status == TenantStatus.ACTIVE
    # Upsert semantics: namespace/postgres/keycloak operators ran twice without error.
    assert operators["namespace"].provisioned.count(failed.tenant_id) == 2


def test_idempotent_reprovision_returns_existing_tenant() -> None:
    store = MetadataStore()  # deterministic local default
    req = TenantCreate(state="ogun", tier="shared")
    first = store.create_tenant(req)
    second = store.create_tenant(req)
    assert first.tenant_id == second.tenant_id
    assert second.status == TenantStatus.ACTIVE
    # Local default reproduces the original no-op workflow markers.
    for marker in ("namespace-created", "postgres-rls-applied",
                   "keycloak-realm-imported", "storage-provisioned",
                   "kms-keyring-issued"):
        assert any(marker in entry for entry in second.workflow)


def test_error_messages_are_pii_scrubbed_in_audit() -> None:
    store = MetadataStore(operators=_fake_operators(
        fail_at="postgres", fail_message="constraint failed for nin=12345678901"))
    tenant = store.create_tenant(TenantCreate(state="osun", tier="shared"))
    assert tenant.status == TenantStatus.FAILED
    blob = "\n".join(str(e.detail) for e in store.audit_events()) + "\n" + \
        "\n".join(tenant.workflow)
    assert "12345678901" not in blob
    assert "[REDACTED" in blob


def test_async_mode_returns_provisioning_then_worker_finalizes() -> None:
    store = MetadataStore(provision_mode="async")
    tenant = store.create_tenant(TenantCreate(state="lagos", tier="shared"))
    assert tenant.status == TenantStatus.PROVISIONING
    assert store._worker is not None
    store._worker.join(timeout=10)
    final = store.get_tenant(tenant.tenant_id)
    assert final is not None and final.status == TenantStatus.ACTIVE
    types = [e.event_type for e in store.audit_events()]
    assert "ng.sos.tenant.provisioned" in types
