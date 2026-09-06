"""Tests for live registry clients (NIMC/CAC) and KYC_REGISTRY_MODE wiring.

Uses httpx MockTransport — no real network. Skips gracefully if httpx is
unavailable.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from app.adapters import AdapterUnavailableError
from app.adapters.registry_clients import CacClient, NimcClient
from app.main import REGISTRY_LIVE_ENV_VARS, build_registry_adapters

NIN = "12345678901"
RC = "RC1234567"

LIVE_ENV = {
    "KYC_REGISTRY_MODE": "live",
    "NIMC_BASE_URL": "https://nimc.example",
    "NIMC_CLIENT_ID": "nimc-id",
    "NIMC_CLIENT_SECRET": "nimc-secret",
    "CAC_BASE_URL": "https://cac.example",
    "CAC_CLIENT_ID": "cac-id",
    "CAC_CLIENT_SECRET": "cac-secret",
}


def _token_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 600})


def _client(cls, handler, **kw):
    transport = httpx.MockTransport(handler)
    return cls(
        base_url="https://registry.example",
        client_id="id",
        client_secret="secret",
        timeout_s=0.5,
        backoff_base_s=0.001,
        transport=transport,
        **kw,
    )


# ---------------- NIMC success ----------------

def test_nimc_success_match():
    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        return httpx.Response(200, json={"match": True, "confidence": 0.98})

    payload = _client(NimcClient, handler).verify_nin(NIN, "Ada Lovelace", "1990-01-01")
    assert payload["status"] == "MATCH"
    assert payload["confidence"] == pytest.approx(0.98)
    # hashing contract: no raw NIN / name anywhere in the payload
    assert NIN not in str(payload)
    assert "Ada Lovelace" not in str(payload)
    assert set(payload) == {"status", "confidence", "nin_sha256"}


def test_nimc_mismatch_and_not_found():
    for match, expected in ((False, "MISMATCH"), (None, "NOT_FOUND")):
        def handler(request, match=match):
            if request.url.path.endswith("/oauth/token"):
                return _token_ok(request)
            return httpx.Response(200, json={"match": match, "confidence": 0.5})

        payload = _client(NimcClient, handler).verify_nin(NIN, "A B", None)
        assert payload["status"] == expected


# ---------------- CAC success ----------------

def test_cac_lookup_success():
    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        return httpx.Response(200, json={"found": True, "name_match": True, "confidence": 0.9})

    payload = _client(CacClient, handler).lookup_company("Acme Ltd", RC)
    assert payload["status"] == "MATCH"
    assert RC not in str(payload)
    assert "Acme Ltd" not in str(payload)
    assert "rc_sha256" in payload


# ---------------- timeout -> AdapterUnavailableError ----------------

def test_nimc_timeout_fails_closed():
    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdapterUnavailableError):
        _client(NimcClient, handler, max_retries=1).verify_nin(NIN, "A B", None)


# ---------------- 401 -> fail closed ----------------

def test_nimc_401_fails_closed():
    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(AdapterUnavailableError):
        _client(NimcClient, handler).verify_nin(NIN, "A B", None)


def test_token_401_fails_closed():
    def handler(request):
        return httpx.Response(401, json={"error": "bad credentials"})

    with pytest.raises(AdapterUnavailableError):
        _client(NimcClient, handler).verify_nin(NIN, "A B", None)


# ---------------- malformed payload -> UNAVAILABLE ----------------

def test_nimc_malformed_payload_unavailable():
    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        return httpx.Response(200, content=b"<html>not json</html>")

    payload = _client(NimcClient, handler).verify_nin(NIN, "A B", None)
    assert payload["status"] == "UNAVAILABLE"
    assert payload["confidence"] == 0.0


def test_cac_missing_fields_unavailable():
    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        return httpx.Response(200, json={"unexpected": "shape"})

    payload = _client(CacClient, handler).lookup_company("Acme Ltd", RC)
    assert payload["status"] == "UNAVAILABLE"


# ---------------- circuit breaker ----------------

def test_circuit_breaker_opens():
    calls = []

    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        calls.append(1)
        return httpx.Response(500)

    client = _client(NimcClient, handler, max_retries=0, circuit_threshold=2)
    for _ in range(2):
        with pytest.raises(AdapterUnavailableError):
            client.verify_nin(NIN, "A B", None)
    with pytest.raises(AdapterUnavailableError, match="circuit breaker"):
        client.verify_nin(NIN, "A B", None)
    assert len(calls) == 2  # no third network call once the breaker is open


# ---------------- adapter compatibility ----------------

def test_nimc_client_wires_into_adapter():
    from app.adapters.registry_adapters import NimcAdapter
    from app.domain import RegistryStatus

    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            return _token_ok(request)
        return httpx.Response(200, json={"match": True, "confidence": 0.95})

    adapter = NimcAdapter(enabled=True, client=_client(NimcClient, handler))
    result = adapter.verify_identity(NIN, "Ada Lovelace", "1990-01-01")
    assert result.status == RegistryStatus.MATCH
    assert NIN not in result.response_hash  # hash only, never raw NIN


# ---------------- boot-failure matrix: KYC_REGISTRY_MODE=live ----------------

def test_fixture_mode_default():
    adapters = build_registry_adapters({})
    assert adapters["corporate_registry"].__class__.__name__ == "FixtureRegistryAdapter"


def test_unknown_mode_boot_fails():
    with pytest.raises(RuntimeError, match="unknown KYC_REGISTRY_MODE"):
        build_registry_adapters({"KYC_REGISTRY_MODE": "bogus"})


@pytest.mark.parametrize(
    "missing",
    [pytest.param(var, id=var) for var in REGISTRY_LIVE_ENV_VARS],
)
def test_live_mode_boot_fails_listing_each_missing_var(missing):
    env = {k: v for k, v in LIVE_ENV.items() if k != missing}
    with pytest.raises(RuntimeError) as excinfo:
        build_registry_adapters(env)
    message = str(excinfo.value)
    assert missing in message
    for var in REGISTRY_LIVE_ENV_VARS:
        if var != missing:
            assert var not in message  # lists only the missing vars


def test_live_mode_all_missing_lists_all():
    with pytest.raises(RuntimeError) as excinfo:
        build_registry_adapters({"KYC_REGISTRY_MODE": "live"})
    for var in REGISTRY_LIVE_ENV_VARS:
        assert var in str(excinfo.value)


def test_live_mode_success_wires_enabled_adapters():
    adapters = build_registry_adapters(dict(LIVE_ENV))
    assert adapters["identity_registry"].enabled is True
    assert adapters["corporate_registry"].enabled is True
    # No live sanctions provider: seam remains fail-closed.
    assert adapters["sanctions"].enabled is False
