"""pytest suite for mod-identity: registration, consent lifecycle, metered
verification with/without consent, revocation enforcement, tenant isolation,
settlement split, audit-trail integrity."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ConsentGrant, VerificationProduct, utcnow
from app.repo import InMemoryIdentityRepository
from app.service import ConsentError, IdentityService, TenantIsolationError


@pytest.fixture()
def svc():
    return IdentityService(InMemoryIdentityRepository())


@pytest.fixture()
def client(svc):
    return TestClient(create_app(svc.repo))


def _seed(svc, state="lagos"):
    svc.register_resident(
        _resident("R1", state, nin="NIN-001", name="Adaeze Okafor", address="12 Marina Rd, Lagos Island")
    )
    svc.register_consumer(_consumer("C1", state, "Sterling Bank"))


def _resident(rid, state, nin="NIN-X", name="Resident", address="Addr"):
    from app.models import Resident

    return Resident(resident_id=rid, state_id=state, nin=nin, full_name=name, address=address)


def _consumer(cid, state, name="Consumer"):
    from app.models import ApiConsumer

    return ApiConsumer(consumer_id=cid, state_id=state, name=name)


def _grant(gid, state, rid, cid, product, days=90):
    now = utcnow()
    return ConsentGrant(
        grant_id=gid,
        state_id=state,
        resident_id=rid,
        consumer_id=cid,
        purpose=product,
        created_at=now,
        expires_at=now + timedelta(days=days),
    )


# -- registration -------------------------------------------------------------

def test_resident_and_consumer_registration(svc):
    _seed(svc)
    assert svc.repo.get_resident("R1").nin == "NIN-001"
    assert svc.repo.get_consumer("C1").name == "Sterling Bank"


def test_http_registration(client):
    r = client.post("/residents", json={
        "resident_id": "R9", "state_id": "lagos", "nin": "NIN-9",
        "full_name": "Test Person", "address": "1 Broad St",
    })
    assert r.status_code == 201
    r = client.get("/residents/R9", params={"state_id": "lagos"})
    assert r.status_code == 200
    r = client.get("/residents/R9", params={"state_id": "ogun"})
    assert r.status_code == 403


# -- consent lifecycle --------------------------------------------------------

def test_consent_grant_and_expiry(svc):
    _seed(svc)
    grant = _grant("G1", "lagos", "R1", "C1", VerificationProduct.ADDRESS_VERIFICATION)
    svc.grant_consent(grant)
    assert grant.is_active()
    expired = _grant("G2", "lagos", "R1", "C1", VerificationProduct.KYC_ADJUNCT, days=-1)
    assert not expired.is_active()


def test_consent_requires_known_parties(svc):
    _seed(svc)
    with pytest.raises(Exception):
        svc.grant_consent(_grant("G3", "lagos", "GHOST", "C1", VerificationProduct.KYC_ADJUNCT))


# -- metered verification -------------------------------------------------------

def test_verification_without_consent_denied_and_audited(svc):
    _seed(svc)
    with pytest.raises(ConsentError):
        svc.verify("lagos", "C1", "R1", VerificationProduct.ADDRESS_VERIFICATION, "12 Marina Rd, Lagos Island")
    actions = [e.action for e in svc.repo.list_audit("lagos")]
    assert "VERIFY_DENIED_NO_CONSENT" in actions


def test_verification_with_consent_meters_usage(svc):
    _seed(svc)
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.ADDRESS_VERIFICATION))
    res = svc.verify("lagos", "C1", "R1", VerificationProduct.ADDRESS_VERIFICATION, "12 Marina Rd, Lagos Island")
    assert res.attested is True
    res2 = svc.verify("lagos", "C1", "R1", VerificationProduct.ADDRESS_VERIFICATION, "wrong address")
    assert res2.attested is False
    usage = svc.repo.list_usage("lagos", "C1")
    assert len(usage) == 2 and all(u.fee_kobo == 5_000 for u in usage)


def test_verification_returns_no_personal_data(client):
    r = client.post("/residents", json={
        "resident_id": "R1", "state_id": "lagos", "nin": "NIN-001",
        "full_name": "Adaeze Okafor", "address": "12 Marina Rd",
    })
    assert r.status_code == 201
    client.post("/consumers", json={"consumer_id": "C1", "state_id": "lagos", "name": "Bank"})
    now = utcnow()
    client.post("/consents", json={
        "grant_id": "G1", "state_id": "lagos", "resident_id": "R1", "consumer_id": "C1",
        "purpose": "KYC_ADJUNCT", "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    })
    res = client.post("/verify", json={
        "state_id": "lagos", "consumer_id": "C1", "resident_id": "R1", "product": "KYC_ADJUNCT",
    })
    assert res.status_code == 200
    body = res.text
    for secret in ("NIN-001", "Adaeze", "Marina"):
        assert secret not in body  # NDPA data minimization


# -- revocation -----------------------------------------------------------------

def test_revocation_blocks_future_calls(svc):
    _seed(svc)
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.KYC_ADJUNCT))
    assert svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT).attested
    svc.revoke_consent("G1", "lagos")
    with pytest.raises(ConsentError):
        svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT)


def test_purpose_scoping(svc):
    _seed(svc)
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.KYC_ADJUNCT))
    with pytest.raises(ConsentError):  # consent is purpose-scoped
        svc.verify("lagos", "C1", "R1", VerificationProduct.ADDRESS_VERIFICATION, "x")


# -- tenant isolation -------------------------------------------------------------

def test_tenant_isolation(svc):
    _seed(svc, state="lagos")
    svc.register_resident(_resident("R2", "ogun", address="Abeokuta"))
    svc.register_consumer(_consumer("C2", "ogun"))
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.KYC_ADJUNCT))
    # Lagos consumer cannot verify Ogun resident
    with pytest.raises(TenantIsolationError):
        svc.verify("lagos", "C1", "R2", VerificationProduct.KYC_ADJUNCT)
    # Ogun consumer cannot touch Lagos consent
    with pytest.raises(TenantIsolationError):
        svc.revoke_consent("G1", "ogun")
    with pytest.raises(TenantIsolationError):
        svc.settle_consumer("ogun", "C1")


# -- settlement -----------------------------------------------------------------

def test_settlement_split_and_once_only(svc):
    _seed(svc)
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.KYC_ADJUNCT))
    svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT)
    svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT)
    stl = svc.settle_consumer("lagos", "C1")
    assert stl.total_kobo == 30_000
    state_line = next(l for l in stl.lines if l.tigerbeetle_account_code == 3001)
    platform_line = next(l for l in stl.lines if l.tigerbeetle_account_code == 2099)
    assert state_line.amount_kobo == 21_000 and platform_line.amount_kobo == 9_000
    assert platform_line.transfer_code == 103
    # usage marked settled — a second settle finds nothing
    with pytest.raises(ValueError):
        svc.settle_consumer("lagos", "C1")


# -- audit ------------------------------------------------------------------------

def test_audit_chain_integrity(svc):
    _seed(svc)
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.KYC_ADJUNCT))
    svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT)
    svc.revoke_consent("G1", "lagos")
    assert svc.verify_audit_chain() is True
    # tamper with an entry — chain must break
    svc.repo._audit[1].details = "tampered"
    assert svc.verify_audit_chain() is False


def test_audit_append_only_via_http(client):
    r = client.get("/audit/verify")
    assert r.json() == {"chain_valid": True}


# -- deceased citizen lifecycle (ResidentStatus) --------------------------------

from datetime import date, datetime, timezone  # noqa: E402

from app.models import GuardianLink, ResidentStatus  # noqa: E402
from app.service import (  # noqa: E402
    GuardianshipError,
    InvalidTransitionError,
    RegistrarRoleError,
    _age_years,
)


def test_status_endpoint_marks_deceased_and_audits(client, svc):
    _seed(svc)
    resp = client.post(
        "/residents/R1/status",
        json={
            "state_id": "lagos", "to_status": "DECEASED",
            "actor_id": "reg-1", "actor_role": "registrar",
            "document_ref": "DEATH-CERT-2026-001",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "DECEASED" and body["active"] is False
    actions = [e.action for e in svc.repo.list_audit()]
    assert "RESIDENT_STATUS_CHANGED" in actions
    assert svc.verify_audit_chain()


def test_verify_attests_false_for_deceased(client, svc):
    _seed(svc)
    svc.grant_consent(_grant("G1", "lagos", "R1", "C1", VerificationProduct.RESIDENCY_ATTESTATION))
    svc.set_resident_status("R1", "lagos", ResidentStatus.DECEASED, "reg-1", "registrar", "DC-1")
    resp = client.post("/verify", json={
        "state_id": "lagos", "consumer_id": "C1", "resident_id": "R1",
        "product": "RESIDENCY_ATTESTATION",
    })
    assert resp.status_code == 200
    assert resp.json()["attested"] is False
    # Address verification also fails for non-ACTIVE residents.
    svc.grant_consent(_grant("G2", "lagos", "R1", "C1", VerificationProduct.ADDRESS_VERIFICATION))
    resp = client.post("/verify", json={
        "state_id": "lagos", "consumer_id": "C1", "resident_id": "R1",
        "product": "ADDRESS_VERIFICATION", "claim": "12 Marina Rd, Lagos Island",
    })
    assert resp.json()["attested"] is False


def test_status_change_requires_registrar_role(client, svc):
    _seed(svc)
    resp = client.post(
        "/residents/R1/status",
        json={
            "state_id": "lagos", "to_status": "SUSPENDED",
            "actor_id": "op-9", "actor_role": "clerk",
        },
    )
    assert resp.status_code == 403
    assert svc.repo.get_resident("R1").status is ResidentStatus.ACTIVE
    assert any(e.action == "RESIDENT_STATUS_DENIED" for e in svc.repo.list_audit())


def test_deceased_requires_death_certificate_ref(client, svc):
    _seed(svc)
    resp = client.post(
        "/residents/R1/status",
        json={
            "state_id": "lagos", "to_status": "DECEASED",
            "actor_id": "reg-1", "actor_role": "registrar",
        },
    )
    assert resp.status_code == 422


def test_invalid_status_transition_409(client, svc):
    _seed(svc)
    svc.set_resident_status("R1", "lagos", ResidentStatus.DECEASED, "reg-1", "registrar", "DC-1")
    resp = client.post(
        "/residents/R1/status",
        json={
            "state_id": "lagos", "to_status": "ACTIVE",
            "actor_id": "reg-1", "actor_role": "registrar",
        },
    )
    assert resp.status_code == 409
    # ACTIVE -> ACTIVE is also not a legal transition.
    _seed2 = _resident("R2", "lagos")
    svc.register_resident(_seed2)
    with pytest.raises(InvalidTransitionError):
        svc.set_resident_status("R2", "lagos", ResidentStatus.ACTIVE, "r", "registrar")


def test_suspend_reactivate_roundtrip(svc):
    _seed(svc)
    svc.set_resident_status("R1", "lagos", ResidentStatus.SUSPENDED, "r", "registrar")
    assert svc.repo.get_resident("R1").active is False
    svc.set_resident_status("R1", "lagos", ResidentStatus.ACTIVE, "r", "registrar")
    assert svc.repo.get_resident("R1").active is True


# -- minors / guardianship --------------------------------------------------------

CLOCK = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _minor_svc():
    svc = IdentityService(InMemoryIdentityRepository(), clock=lambda: CLOCK)
    svc.register_resident(_resident("M1", "lagos"))
    svc.register_resident(_resident("G1R", "lagos"))
    # Set DOBs directly on the stored records (injected clock drives age).
    svc.repo.get_resident("M1").date_of_birth = date(2012, 1, 15)  # 14y
    svc.repo.get_resident("G1R").date_of_birth = date(1980, 5, 1)
    svc.register_consumer(_consumer("C1", "lagos", "Sterling Bank"))
    return svc


def test_minor_consent_without_guardian_rejected():
    svc = _minor_svc()
    with pytest.raises(GuardianshipError):
        svc.grant_consent(_grant("GX", "lagos", "M1", "C1", VerificationProduct.RESIDENCY_ATTESTATION))
    assert any(e.action == "CONSENT_DENIED_NO_GUARDIAN" for e in svc.repo.list_audit())


def test_minor_consent_with_guardian_passes():
    svc = _minor_svc()
    svc.add_guardian_link(GuardianLink(
        state_id="lagos", resident_id="M1", guardian_resident_id="G1R",
        kyc_case_ref="KYC-VERIFIED-FIXTURE",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    ))
    grant = svc.grant_consent(_grant("GY", "lagos", "M1", "C1", VerificationProduct.RESIDENCY_ATTESTATION))
    assert grant.grant_id == "GY"


def test_minor_consent_endpoint_409():
    svc = _minor_svc()
    client = TestClient(create_app(svc.repo))
    # create_app builds its own service; replicate via HTTP instead.
    resp = client.post("/consents", json=_grant("GZ", "lagos", "M1", "C1", VerificationProduct.RESIDENCY_ATTESTATION).model_dump(mode="json"))
    assert resp.status_code == 409


def test_guardian_link_auto_expires_at_18():
    # Resident turns 18 on 2030-01-15; at 2030-06-01 they are an adult and
    # no guardian link is needed (guardianship semantics auto-expire).
    adult_clock = datetime(2030, 6, 1, tzinfo=timezone.utc)
    svc = IdentityService(InMemoryIdentityRepository(), clock=lambda: adult_clock)
    svc.register_resident(_resident("M2", "lagos"))
    svc.repo.get_resident("M2").date_of_birth = date(2012, 1, 15)
    svc.register_consumer(_consumer("C1", "lagos", "Bank"))
    grant = svc.grant_consent(_grant("GW", "lagos", "M2", "C1", VerificationProduct.RESIDENCY_ATTESTATION))
    assert grant.grant_id == "GW"
    # While still a minor, an expired guardian link does not help.
    svc2 = _minor_svc()
    svc2.add_guardian_link(GuardianLink(
        state_id="lagos", resident_id="M1", guardian_resident_id="G1R",
        kyc_case_ref="KYC-1", expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    ))
    with pytest.raises(GuardianshipError):
        svc2.grant_consent(_grant("GV", "lagos", "M1", "C1", VerificationProduct.RESIDENCY_ATTESTATION))


def test_age_years_helper():
    assert _age_years(date(2012, 1, 15), date(2026, 1, 14)) == 13
    assert _age_years(date(2012, 1, 15), date(2030, 1, 15)) == 18
