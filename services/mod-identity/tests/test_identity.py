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
