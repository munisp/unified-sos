"""Officer provisioning: lifecycle, audit, events, fixture Keycloak seam."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.officers import (
    FixtureKeycloakAdmin,
    OfficerConfigurationError,
    OfficerRegistry,
    STAKEHOLDER_ROLES,
    build_keycloak_admin,
)


@pytest.fixture()
def kc() -> FixtureKeycloakAdmin:
    return FixtureKeycloakAdmin()


@pytest.fixture()
def client(kc: FixtureKeycloakAdmin) -> TestClient:
    from app.domain import MetadataStore

    store = MetadataStore()
    app = create_app(store=store, officers=OfficerRegistry(store, kc))
    c = TestClient(app)
    resp = c.post("/control/v1/tenants", json={"state": "ogun", "tier": "shared"})
    assert resp.status_code == 202
    return c


def _invite(client: TestClient, email="ada@og.gov.ng", role="registry",
            name="Ada Obi") -> dict:
    resp = client.post("/cp/v1/tenants/ogun/officers", json={
        "email": email, "display_name": name, "role": role})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _events(client: TestClient) -> list[dict]:
    return client.get("/control/v1/audit-events").json()["events"]


def test_invite_issues_temporary_credential(client, kc):
    officer = _invite(client)
    assert officer["status"] == "invited"
    assert officer["role"] == "registry"
    assert officer["temporary_credential"].startswith("tmp-")
    assert officer["groups"] == list(STAKEHOLDER_ROLES["registry"])
    # Fixture Keycloak seam received the user.
    kc_user = kc.users[officer["keycloak_user_id"]]
    assert kc_user["email"] == "ada@og.gov.ng"
    assert kc_user["enabled"] is True


def test_invite_rejects_unknown_role(client):
    resp = client.post("/cp/v1/tenants/ogun/officers", json={
        "email": "x@og.gov.ng", "display_name": "X", "role": "superuser"})
    assert resp.status_code == 422


def test_all_catalog_roles_invite(client):
    for i, role in enumerate(sorted(STAKEHOLDER_ROLES)):
        officer = _invite(client, email=f"u{i}@og.gov.ng", role=role)
        assert officer["status"] == "invited"


def test_duplicate_active_email_rejected(client):
    _invite(client)
    resp = client.post("/cp/v1/tenants/ogun/officers", json={
        "email": "ada@og.gov.ng", "display_name": "Ada Clone", "role": "surveyor"})
    assert resp.status_code == 422


def test_invite_unknown_tenant_404(client):
    resp = client.post("/cp/v1/tenants/kano/officers", json={
        "email": "a@b.ng", "display_name": "A", "role": "registry"})
    assert resp.status_code == 404


def test_full_lifecycle_and_audit_events(client):
    officer = _invite(client)
    oid = officer["officer_id"]
    r = client.post(f"/cp/v1/tenants/ogun/officers/{oid}/activate")
    assert r.status_code == 200 and r.json()["status"] == "active"
    r = client.post(f"/cp/v1/tenants/ogun/officers/{oid}/suspend")
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    r = client.post(f"/cp/v1/tenants/ogun/officers/{oid}/activate")
    assert r.status_code == 200 and r.json()["status"] == "active"
    r = client.post(f"/cp/v1/tenants/ogun/officers/{oid}/offboard")
    assert r.status_code == 200 and r.json()["status"] == "offboarded"
    types = [e["event_type"] for e in _events(client)]
    for expected in (
        "ng.sos.tenant.officer_invited", "ng.sos.tenant.officer_activated",
        "ng.sos.tenant.officer_suspended", "ng.sos.tenant.officer_offboarded",
    ):
        assert expected in types


def test_audit_events_are_hash_chained(client):
    officer = _invite(client)
    client.post(f"/cp/v1/tenants/ogun/officers/{officer['officer_id']}/activate")
    events = [e for e in _events(client) if e["event_type"].startswith("ng.sos.tenant")]
    assert all(e["event_hash"] for e in events)
    # Sequential events on the tenant chain link via prev_hash.
    by_seq = sorted(events, key=lambda e: e["seq"])
    for prev, cur in zip(by_seq, by_seq[1:]):
        assert cur["prev_hash"] == prev["event_hash"]


def test_illegal_transitions(client):
    officer = _invite(client)
    oid = officer["officer_id"]
    # INVITED -> SUSPENDED is illegal.
    assert client.post(f"/cp/v1/tenants/ogun/officers/{oid}/suspend").status_code == 409
    # OFFBOARDED is terminal.
    client.post(f"/cp/v1/tenants/ogun/officers/{oid}/offboard")
    assert client.post(f"/cp/v1/tenants/ogun/officers/{oid}/activate").status_code == 409
    assert client.post(f"/cp/v1/tenants/ogun/officers/{oid}/offboard").status_code == 409


def test_offboard_disables_user_and_revokes_sessions(client, kc):
    officer = _invite(client)
    kc.users[officer["keycloak_user_id"]]["sessions"] = 3
    client.post(f"/cp/v1/tenants/ogun/officers/{officer['officer_id']}/offboard")
    kc_user = kc.users[officer["keycloak_user_id"]]
    assert kc_user["enabled"] is False
    assert kc_user["sessions"] == 0


def test_unknown_and_cross_tenant_officer_404(client):
    officer = _invite(client)
    assert client.post(
        "/cp/v1/tenants/ogun/officers/ofc-999999/activate").status_code == 404
    # A second tenant cannot touch ogun's officer.
    client.post("/control/v1/tenants", json={"state": "lagos", "tier": "shared"})
    assert client.post(
        f"/cp/v1/tenants/lagos/officers/{officer['officer_id']}/activate"
    ).status_code == 404


def test_list_filters(client):
    _invite(client, email="r1@og.gov.ng", role="registry")
    _invite(client, email="s1@og.gov.ng", role="surveyor")
    surveyor = _invite(client, email="s2@og.gov.ng", role="surveyor")
    client.post(f"/cp/v1/tenants/ogun/officers/{surveyor['officer_id']}/activate")
    all_officers = client.get("/cp/v1/tenants/ogun/officers").json()
    assert len(all_officers) == 3
    surveyors = client.get("/cp/v1/tenants/ogun/officers?role=surveyor").json()
    assert {o["email"] for o in surveyors} == {"s1@og.gov.ng", "s2@og.gov.ng"}
    invited = client.get("/cp/v1/tenants/ogun/officers?officer_status=invited").json()
    assert {o["email"] for o in invited} == {"r1@og.gov.ng", "s1@og.gov.ng"}


def test_admin_token_gate(monkeypatch):
    monkeypatch.setenv("SOS_CP_ADMIN_TOKEN", "sekrit")
    from app.domain import MetadataStore

    store = MetadataStore()
    c = TestClient(create_app(store=store,
                              officers=OfficerRegistry(store, FixtureKeycloakAdmin())))
    c.post("/control/v1/tenants", json={"state": "ogun", "tier": "shared"})
    body = {"email": "a@og.gov.ng", "display_name": "A", "role": "registry"}
    assert c.post("/cp/v1/tenants/ogun/officers", json=body).status_code == 403
    ok = c.post("/cp/v1/tenants/ogun/officers", json=body,
                headers={"X-Admin-Token": "sekrit"})
    assert ok.status_code == 201


def test_production_profile_fails_closed(monkeypatch):
    monkeypatch.setenv("SOS_CP_PROFILE", "production")
    for var in ("KEYCLOAK_ADMIN_URL", "KEYCLOAK_ADMIN_USER", "KEYCLOAK_ADMIN_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(OfficerConfigurationError):
        build_keycloak_admin()


def test_fixture_is_default_in_dev(monkeypatch):
    monkeypatch.delenv("SOS_CP_PROFILE", raising=False)
    assert isinstance(build_keycloak_admin({}), FixtureKeycloakAdmin)
