"""Tests for identity federation clients and IDENTITY_FEDERATION_MODE wiring.

Uses httpx MockTransport for the live client — no real network.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from app.federation import (
    FEDERATION_LIVE_ENV_VARS,
    FederationUnavailableError,
    FixtureFederationClient,
    KeycloakFederationClient,
    build_federation_client,
)
from app.models import VerificationProduct
from app.repo import InMemoryIdentityRepository
from app.service import IdentityService

LIVE_ENV = {
    "IDENTITY_FEDERATION_MODE": "live",
    "KEYCLOAK_BASE_URL": "https://keycloak.example",
    "KEYCLOAK_CLIENT_ID": "kc-id",
    "KEYCLOAK_CLIENT_SECRET": "kc-secret",
}


# ---------------- fixture client ----------------

def test_fixture_client_deterministic_and_hashed():
    client = FixtureFederationClient()
    res = client.verify_nin_claim("lagos", "NIN-SECRET-1", "some claim")
    assert res["attested"] is True
    assert "NIN-SECRET-1" not in str(res)
    assert client.verify_nin_claim("lagos", "NIN-SECRET-1", "deny")["attested"] is False
    assert client.verify_nin_claim("lagos", "NIN-SECRET-1", "")["attested"] is True


# ---------------- live client over MockTransport ----------------

def _token_ok(request):
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})


def _live_client(handler, **kw):
    transport = httpx.MockTransport(handler)
    return KeycloakFederationClient(
        base_url="https://keycloak.example",
        client_id="id",
        client_secret="secret",
        timeout_s=0.5,
        backoff_base_s=0.001,
        transport=transport,
        **kw,
    )


def test_live_success():
    def handler(request):
        if request.url.path.endswith("/token"):
            return _token_ok(request)
        return httpx.Response(200, json={"attested": True})

    res = _live_client(handler).verify_nin_claim("lagos", "NIN-9", "claim")
    assert res["attested"] is True
    assert "NIN-9" not in str(res)


def test_live_timeout_fails_closed():
    def handler(request):
        if request.url.path.endswith("/token"):
            return _token_ok(request)
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(FederationUnavailableError):
        _live_client(handler, max_retries=1).verify_nin_claim("lagos", "NIN-9", "c")


def test_live_401_fails_closed():
    def handler(request):
        if request.url.path.endswith("/token"):
            return _token_ok(request)
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(FederationUnavailableError):
        _live_client(handler).verify_nin_claim("lagos", "NIN-9", "c")


def test_live_malformed_fails_closed():
    def handler(request):
        if request.url.path.endswith("/token"):
            return _token_ok(request)
        return httpx.Response(200, content=b"not-json")

    with pytest.raises(FederationUnavailableError):
        _live_client(handler).verify_nin_claim("lagos", "NIN-9", "c")


def test_live_circuit_breaker_opens():
    calls = []

    def handler(request):
        if request.url.path.endswith("/token"):
            return _token_ok(request)
        calls.append(1)
        return httpx.Response(500)

    client = _live_client(handler, max_retries=0, circuit_threshold=2)
    for _ in range(2):
        with pytest.raises(FederationUnavailableError):
            client.verify_nin_claim("lagos", "NIN-9", "c")
    with pytest.raises(FederationUnavailableError, match="circuit breaker"):
        client.verify_nin_claim("lagos", "NIN-9", "c")
    assert len(calls) == 2


# ---------------- boot-failure matrix: IDENTITY_FEDERATION_MODE=live ----------------

def test_fixture_mode_default():
    assert isinstance(build_federation_client({}), FixtureFederationClient)


def test_unknown_mode_boot_fails():
    with pytest.raises(RuntimeError, match="unknown IDENTITY_FEDERATION_MODE"):
        build_federation_client({"IDENTITY_FEDERATION_MODE": "bogus"})


@pytest.mark.parametrize("missing", FEDERATION_LIVE_ENV_VARS)
def test_live_mode_boot_fails_listing_missing_var(missing):
    env = {k: v for k, v in LIVE_ENV.items() if k != missing}
    with pytest.raises(RuntimeError) as excinfo:
        build_federation_client(env)
    assert missing in str(excinfo.value)


def test_live_mode_all_env_builds_keycloak_client():
    assert isinstance(build_federation_client(dict(LIVE_ENV)), KeycloakFederationClient)


# ---------------- metered verification records registry_latency_ms ----------------

def _seeded_service(federation_client=None):
    from app.models import ApiConsumer, ConsentGrant, Resident, utcnow
    from datetime import timedelta

    repo = InMemoryIdentityRepository()
    svc = IdentityService(repo, federation_client or FixtureFederationClient())
    svc.register_resident(
        Resident(resident_id="R1", state_id="lagos", nin="NIN-001",
                 full_name="Adaeze Okafor", address="12 Marina Rd")
    )
    svc.register_consumer(
        ApiConsumer(consumer_id="C1", state_id="lagos", name="Sterling Bank")
    )
    svc.grant_consent(
        ConsentGrant(grant_id="G1", state_id="lagos", resident_id="R1",
                     consumer_id="C1", purpose=VerificationProduct.KYC_ADJUNCT,
                     expires_at=utcnow() + timedelta(days=1))
    )
    return svc, repo


def test_kyc_adjunct_records_registry_latency_ms_in_audit():
    svc, repo = _seeded_service()
    result = svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT, "claim-1")
    assert result.attested is True
    granted = [e for e in repo.list_audit("lagos") if e.action == "VERIFY_GRANTED"]
    assert granted, "expected a VERIFY_GRANTED audit entry"
    assert "registry_latency_ms=" in granted[-1].details
    assert "NIN-001" not in granted[-1].details


def test_kyc_adjunct_federation_denial_blocks_attestation():
    svc, repo = _seeded_service()
    result = svc.verify("lagos", "C1", "R1", VerificationProduct.KYC_ADJUNCT, "deny")
    assert result.attested is False
    granted = [e for e in repo.list_audit("lagos") if e.action == "VERIFY_GRANTED"]
    assert "registry_latency_ms=" in granted[-1].details
