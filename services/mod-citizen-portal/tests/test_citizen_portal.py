"""pytest suite for mod-citizen-portal (CIT-11): NIN masking, SSO sessions,
tenant isolation, service catalog/request lifecycle, expedited fee
settlement split, petitions, ghost-worker detection rules, temporal
workflow reference, OpenAPI generation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import (
    CivilServant,
    GhostRule,
    PetitionStatus,
    Priority,
    RequestStatus,
    ServantStatus,
    hash_nin,
)
from app.main import create_app
from app.repository import InMemoryCitizenPortalRepository
from app.service import CitizenPortalService, InvalidTransitionError, TenantIsolationError

RAW_NIN = "12345678901"


@pytest.fixture()
def svc():
    return CitizenPortalService(InMemoryCitizenPortalRepository())


@pytest.fixture()
def client(svc):
    return TestClient(create_app(svc.repo))


@pytest.fixture()
def wallet(svc):
    return svc.create_wallet("lagos", RAW_NIN)


def _servant(emp, state="lagos", salary=200_000_00, bio=None, bank=None, status=ServantStatus.ACTIVE):
    return CivilServant(
        employee_no=emp,
        state_id=state,
        full_name=f"Worker {emp}",
        mda="Ministry of Works",
        grade_band="GL-10",
        salary_kobo=salary,
        biometric_template_hash=bio or f"bio-{emp}",
        bank_account_hash=bank or f"acct-{emp}",
        status=status,
    )


# -- wallets / NIN masking ---------------------------------------------------


def test_wallet_created_with_hashed_nin(svc, wallet):
    assert wallet.nin_hash == hash_nin(RAW_NIN)
    assert RAW_NIN not in wallet.model_dump_json()


def test_wallet_read_masks_nin(client, wallet):
    resp = client.get(f"/citizen/v1/wallets/{wallet.wallet_id}", params={"state_id": "lagos"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["masked_nin"].startswith("NIN-HASH:")
    assert RAW_NIN not in resp.text
    assert "nin_hash" not in body


def test_wallet_tenant_isolation(client, wallet):
    resp = client.get(f"/citizen/v1/wallets/{wallet.wallet_id}", params={"state_id": "ogun"})
    assert resp.status_code == 403


def test_wallet_missing_404(client):
    assert client.get("/citizen/v1/wallets/WLT-999999", params={"state_id": "lagos"}).status_code == 404


def test_wallet_creation_settles_smartcard_fee(svc, wallet):
    settlements = svc.repo.list_settlements("lagos")
    assert len(settlements) == 1
    st = settlements[0]
    assert st.source == "SMARTCARD_FEE"
    assert sum(line.amount_kobo for line in st.lines) == st.gross_kobo


# -- SSO sessions ---------------------------------------------------------------


def test_sso_session_created(svc, wallet):
    session = svc.create_sso_session("lagos", wallet.wallet_id, "https://portal.lagos.example/cb")
    assert session.realm == "sos-lagos"
    assert session.redirect_uri == "https://portal.lagos.example/cb"
    assert "openid" in session.scopes
    assert not session.is_expired()


def test_sso_session_endpoint_and_tenant_guard(client, wallet):
    resp = client.post(
        "/citizen/v1/sso/sessions",
        json={"state_id": "lagos", "wallet_id": wallet.wallet_id, "redirect_uri": "https://x.example/cb"},
    )
    assert resp.status_code == 201
    bad = client.post(
        "/citizen/v1/sso/sessions",
        json={"state_id": "ogun", "wallet_id": wallet.wallet_id, "redirect_uri": "https://x.example/cb"},
    )
    assert bad.status_code == 403


# -- catalog / service requests ---------------------------------------------------


def test_catalog_seeded_with_all_categories(svc):
    catalog = svc.ensure_catalog("lagos")
    cats = {e.category.value for e in catalog}
    assert cats == {"REVENUE", "LANDS", "HEALTH", "EDUCATION", "MARKET"}
    # idempotent
    assert len(svc.ensure_catalog("lagos")) == len(catalog)
    # per-state isolation of catalogs
    assert svc.repo.list_catalog("ogun") == []


def test_service_request_lifecycle(client, wallet):
    req = client.post(
        "/citizen/v1/service-requests",
        json={"state_id": "lagos", "wallet_id": wallet.wallet_id, "service_code": "REV-TAX-ID",
              "form_payload": {"employer": "Sterling"}},
    ).json()
    assert req["status"] == "SUBMITTED"
    rid = req["request_id"]
    for target in ("IN_REVIEW", "APPROVED", "COMPLETED"):
        resp = client.post(f"/citizen/v1/service-requests/{rid}/advance",
                           json={"state_id": "lagos", "to_status": target})
        assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert [e["status"] for e in body["timeline"]] == ["SUBMITTED", "IN_REVIEW", "APPROVED", "COMPLETED"]


def test_service_request_reject_path(svc, wallet):
    req = svc.submit_service_request("lagos", wallet.wallet_id, "HLT-PHC-REG", {}, Priority.STANDARD)
    svc.advance_service_request(req.request_id, "lagos", RequestStatus.IN_REVIEW)
    req = svc.advance_service_request(req.request_id, "lagos", RequestStatus.REJECTED, "incomplete docs")
    assert req.status is RequestStatus.REJECTED
    with pytest.raises(InvalidTransitionError):
        svc.advance_service_request(req.request_id, "lagos", RequestStatus.APPROVED)


def test_service_request_tenant_isolation(client, wallet):
    rid = client.post(
        "/citizen/v1/service-requests",
        json={"state_id": "lagos", "wallet_id": wallet.wallet_id, "service_code": "REV-TAX-ID"},
    ).json()["request_id"]
    assert client.get(f"/citizen/v1/service-requests/{rid}", params={"state_id": "ogun"}).status_code == 403


def test_unknown_service_code_404(client, wallet):
    resp = client.post(
        "/citizen/v1/service-requests",
        json={"state_id": "lagos", "wallet_id": wallet.wallet_id, "service_code": "NOPE-1"},
    )
    assert resp.status_code == 404


# -- expedited fee settlement ------------------------------------------------------


def test_expedited_fee_and_default_split(svc, wallet):
    svc.ensure_catalog("lagos")
    req = svc.submit_service_request("lagos", wallet.wallet_id, "MKT-STALL", {}, Priority.EXPEDITED)
    entry = svc.repo.get_catalog_entry("lagos", "MKT-STALL")
    assert req.fee_kobo == entry.expedited_fee_kobo
    st = [s for s in svc.repo.list_settlements("lagos") if s.source == "EXPEDITED_FEE"][0]
    by_payee = {line.payee: line.amount_kobo for line in st.lines}
    assert by_payee["state_tsa"] == req.fee_kobo * 7_000 // 10_000
    assert by_payee["mda_vote"] == req.fee_kobo * 1_500 // 10_000
    assert sum(by_payee.values()) == req.fee_kobo  # remainder lands on last line


def test_state_split_override(svc, wallet):
    svc._state_splits["lagos"] = {"state_tsa": 8_000, "mda_vote": 1_000, "platform_escrow": 1_000}
    svc.ensure_catalog("lagos")
    req = svc.submit_service_request("lagos", wallet.wallet_id, "REV-TAX-ID", {}, Priority.EXPEDITED)
    st = [s for s in svc.repo.list_settlements("lagos") if s.source == "EXPEDITED_FEE"][0]
    by_payee = {line.payee: line.amount_kobo for line in st.lines}
    assert by_payee["state_tsa"] == req.fee_kobo * 8_000 // 10_000


# -- petitions -----------------------------------------------------------------


def test_petition_workflow_and_reference(svc, wallet):
    pet = svc.submit_petition("lagos", wallet.wallet_id, "Fix CMS bus stop", "Flooded every rain.")
    assert pet.reference_id.startswith("PET-LAGOS-")
    pet = svc.advance_petition(pet.petition_id, "lagos", PetitionStatus.UNDER_REVIEW)
    pet = svc.advance_petition(pet.petition_id, "lagos", PetitionStatus.RESOLVED)
    assert pet.status is PetitionStatus.RESOLVED
    with pytest.raises(InvalidTransitionError):
        svc.advance_petition(pet.petition_id, "lagos", PetitionStatus.UNDER_REVIEW)


def test_petition_tenant_isolation(svc, wallet):
    pet = svc.submit_petition("lagos", wallet.wallet_id, "T", "B")
    with pytest.raises(TenantIsolationError):
        svc.advance_petition(pet.petition_id, "ogun", PetitionStatus.UNDER_REVIEW)


# -- civil-service clean-up -------------------------------------------------------


def test_unverified_biometric_rule(svc):
    svc.register_civil_servant(_servant("E1"))
    svc.register_civil_servant(_servant("E2"))
    svc.record_biometric_verification("lagos", "E1", liveness_passed=True, verified=True)
    audit = svc.run_payroll_audit("lagos")
    unverified = [f for f in audit.findings if f.rule is GhostRule.UNVERIFIED_BIOMETRIC]
    assert len(unverified) == 1
    assert unverified[0].employee_nos == ["E2"]
    assert unverified[0].recoverable_kobo == 200_000_00


def test_duplicate_biometric_rule(svc):
    for emp in ("E1", "E2", "E3"):
        svc.register_civil_servant(_servant(emp, bio="bio-SHARED"))
        svc.record_biometric_verification("lagos", emp, liveness_passed=True, verified=True)
    audit = svc.run_payroll_audit("lagos")
    dup = [f for f in audit.findings if f.rule is GhostRule.DUPLICATE_BIOMETRIC][0]
    assert dup.employee_nos == ["E1", "E2", "E3"]
    assert dup.recoverable_kobo == 2 * 200_000_00  # first record kept


def test_duplicate_salary_account_rule(svc):
    svc.register_civil_servant(_servant("E1", bank="acct-SHARED"))
    svc.register_civil_servant(_servant("E2", bank="acct-SHARED"))
    audit = svc.run_payroll_audit("lagos")
    dup = [f for f in audit.findings if f.rule is GhostRule.DUPLICATE_SALARY_ACCOUNT][0]
    assert dup.recoverable_kobo == 200_000_00


def test_inactive_still_paid_rule(svc):
    svc.register_civil_servant(_servant("E1", status=ServantStatus.RETIRED))
    svc.register_civil_servant(_servant("E2", status=ServantStatus.INACTIVE))
    svc.register_civil_servant(_servant("E3"))
    svc.record_biometric_verification("lagos", "E3", liveness_passed=True, verified=True)
    audit = svc.run_payroll_audit("lagos")
    ina = [f for f in audit.findings if f.rule is GhostRule.INACTIVE_STILL_PAID][0]
    assert ina.employee_nos == ["E1", "E2"]
    assert ina.recoverable_kobo == 2 * 200_000_00


def test_payroll_audit_totals_temporal_ref_and_isolation(svc, client):
    svc.register_civil_servant(_servant("E1"))  # unverified
    audit = svc.run_payroll_audit("lagos")
    assert audit.total_recoverable_kobo == sum(f.recoverable_kobo for f in audit.findings)
    assert audit.temporal_workflow is not None
    assert audit.temporal_workflow.task_queue == "payroll-cleanup"
    resp = client.get(f"/citizen/v1/payroll-audits/{audit.audit_id}", params={"state_id": "lagos"})
    assert resp.status_code == 200
    assert client.get(f"/citizen/v1/payroll-audits/{audit.audit_id}",
                      params={"state_id": "ogun"}).status_code == 403


def test_biometric_verification_requires_liveness(svc):
    svc.register_civil_servant(_servant("E1"))
    v = svc.record_biometric_verification("lagos", "E1", liveness_passed=False, verified=True)
    assert v.verified is False  # liveness gate enforced


def test_audit_deterministic(svc):
    svc.register_civil_servant(_servant("E1", bio="bio-X"))
    svc.register_civil_servant(_servant("E2", bio="bio-X"))
    a1 = svc.run_payroll_audit("lagos")
    a2 = svc.run_payroll_audit("lagos")
    assert [(f.rule, f.employee_nos, f.recoverable_kobo) for f in a1.findings] == [
        (f.rule, f.employee_nos, f.recoverable_kobo) for f in a2.findings
    ]


# -- platform -----------------------------------------------------------------


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok", "module": "mod-citizen-portal"}


def test_openapi_generation(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for path in (
        "/citizen/v1/wallets",
        "/citizen/v1/sso/sessions",
        "/citizen/v1/services",
        "/citizen/v1/service-requests",
        "/citizen/v1/petitions",
        "/citizen/v1/civil-servants",
        "/citizen/v1/biometric-verifications",
        "/citizen/v1/payroll-audits",
        "/healthz",
    ):
        assert path in paths, path
