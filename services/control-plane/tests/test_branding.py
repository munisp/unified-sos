"""Tests for the per-state whitelabel branding registry and endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.branding import (
    BRANDING_UPDATED_CHANNEL,
    Branding,
    BrandingRegistry,
    default_custom_domain,
)
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
STATES_CONFIG = REPO_ROOT / "config" / "states"

ADMIN = "test-admin-token"


@pytest.fixture()
def registry() -> BrandingRegistry:
    return BrandingRegistry(config_root=STATES_CONFIG)


@pytest.fixture()
def client(registry: BrandingRegistry, monkeypatch) -> TestClient:
    monkeypatch.setenv("SOS_CP_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("SOS_PROFILE", "production")
    return TestClient(create_app(branding=registry))


def valid_branding(state: str = "lagos") -> dict:
    return json.loads((STATES_CONFIG / state / "branding.json").read_text())


# --- validation -------------------------------------------------------------
def test_branding_rejects_bad_hex_color() -> None:
    doc = valid_branding()
    doc["primary_color"] = "blue"
    with pytest.raises(ValueError, match="hex color"):
        Branding(**doc)


def test_branding_rejects_bad_domain() -> None:
    doc = valid_branding()
    doc["custom_domain"] = "https://sos.lagosstate.gov.ng/path"
    with pytest.raises(ValueError, match="hostname"):
        Branding(**doc)


def test_branding_rejects_unsupported_locale() -> None:
    doc = valid_branding()
    doc["locales"] = ["en", "fr"]
    with pytest.raises(ValueError, match="unsupported locales"):
        Branding(**doc)


def test_branding_default_locale_must_be_in_locales() -> None:
    doc = valid_branding()
    doc["locales"] = ["en"]
    doc["default_locale"] = "yo"
    with pytest.raises(ValueError, match="must be in locales"):
        Branding(**doc)


# --- seed loading -------------------------------------------------------------
def test_all_37_seeds_load(registry: BrandingRegistry) -> None:
    all_branding = registry.list_all()
    assert len(all_branding) == 37
    states = {b.tenant_state_id for b in all_branding}
    assert {"lagos", "fct", "kano", "zamfara"} <= states
    lagos = registry.get("lagos")
    assert lagos is not None
    assert lagos.custom_domain == "sos.lagosstate.gov.ng"
    assert lagos.default_locale == "yo"
    fct = registry.get("fct")
    assert fct is not None and fct.custom_domain == "sos.fct.gov.ng"
    kano = registry.get("kano")
    assert kano is not None and set(kano.locales) == {"ha", "en"}
    # every seed's domain matches the directory naming pattern
    for b in all_branding:
        expected = "sos.fct.gov.ng" if b.tenant_state_id == "fct" else (
            f"sos.{b.tenant_state_id.replace('_', '')}state.gov.ng"
        )
        assert b.custom_domain == expected
        assert b.logo_url == f"/branding/{b.tenant_state_id}/logo.svg"


def test_default_custom_domain_pattern() -> None:
    assert default_custom_domain("lagos") == "sos.lagosstate.gov.ng"
    assert default_custom_domain("fct") == "sos.fct.gov.ng"
    assert default_custom_domain("cross_river") == "sos.crossriverstate.gov.ng"


# --- endpoints -------------------------------------------------------------
def test_public_read_unauthenticated(client: TestClient) -> None:
    resp = client.get("/cp/v1/tenants/lagos/branding")
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Lagos State"
    assert resp.json()["portal_title"] == "Lagos State One-Gov Portal"
    assert client.get("/cp/v1/tenants/nope/branding").status_code == 404


def test_list_all_branding(client: TestClient) -> None:
    resp = client.get("/cp/v1/branding")
    assert resp.status_code == 200
    assert len(resp.json()) == 37


def test_put_requires_admin_token(client: TestClient) -> None:
    doc = valid_branding()
    assert client.put("/cp/v1/tenants/lagos/branding", json=doc).status_code == 403
    resp = client.put("/cp/v1/tenants/lagos/branding", json=doc,
                      headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 403


def test_production_profile_fail_closed_without_token(registry: BrandingRegistry,
                                                      monkeypatch) -> None:
    monkeypatch.delenv("SOS_CP_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("SOS_PROFILE", "production")
    client = TestClient(create_app(branding=registry))
    resp = client.put("/cp/v1/tenants/lagos/branding", json=valid_branding())
    assert resp.status_code == 403
    assert "fail-closed" in resp.json()["detail"]


def test_put_updates_and_overrides_seed(client: TestClient) -> None:
    doc = valid_branding()
    doc["portal_title"] = "Lagos State Revenue Portal"
    doc["tagline"] = "Pay every Lagos levy in one place."
    resp = client.put("/cp/v1/tenants/lagos/branding", json=doc,
                      headers={"X-Admin-Token": ADMIN})
    assert resp.status_code == 200, resp.text
    got = client.get("/cp/v1/tenants/lagos/branding").json()
    assert got["portal_title"] == "Lagos State Revenue Portal"


def test_put_rejects_state_mismatch(client: TestClient) -> None:
    doc = valid_branding("ogun")
    resp = client.put("/cp/v1/tenants/lagos/branding", json=doc,
                      headers={"X-Admin-Token": ADMIN})
    assert resp.status_code == 422


def test_put_rejects_invalid_payload(client: TestClient) -> None:
    doc = valid_branding()
    doc["secondary_color"] = "#12345"  # not #RRGGBB
    resp = client.put("/cp/v1/tenants/lagos/branding", json=doc,
                      headers={"X-Admin-Token": ADMIN})
    assert resp.status_code == 422


# --- audit chain + event -----------------------------------------------------
def test_update_appends_hash_chain_and_publishes(client: TestClient,
                                                 registry: BrandingRegistry) -> None:
    doc = valid_branding()
    for title in ("One-Gov Portal v2", "One-Gov Portal v3"):
        doc["portal_title"] = title
        assert client.put("/cp/v1/tenants/lagos/branding", json=doc,
                          headers={"X-Admin-Token": ADMIN}).status_code == 200
    entries = registry.audit_entries()
    assert len(entries) == 2
    assert entries[1].prev_hash == entries[0].event_hash
    assert registry.verify_audit_chain() == []
    # tamper detection
    entries[0].updated_by = "mallory"
    assert registry.verify_audit_chain() != []
    # event published per update
    published = [e for e in registry._bus.published
                 if e["topic"] == BRANDING_UPDATED_CHANNEL]
    assert len(published) == 2
    payload = published[-1]["payload"]
    assert payload["state_id"] == "lagos"
    assert payload["entry_hash"] == entries[1].event_hash
    assert set(payload) == {"state_id", "updated_by", "entry_hash", "timestamp"}


# --- domain verify -------------------------------------------------------------
def test_domain_verify_mapping(client: TestClient) -> None:
    resp = client.post("/cp/v1/domains/verify",
                       json={"custom_domain": "sos.lagosstate.gov.ng"})
    assert resp.status_code == 200
    assert resp.json()["tenant_state_id"] == "lagos"
    # case-insensitive, FCT special pattern
    resp = client.post("/cp/v1/domains/verify",
                       json={"custom_domain": "SOS.FCT.GOV.NG"})
    assert resp.status_code == 200
    assert resp.json()["tenant_state_id"] == "fct"
    assert client.post("/cp/v1/domains/verify",
                       json={"custom_domain": "evil.example.com"}).status_code == 404


def test_domain_verify_follows_override(client: TestClient) -> None:
    doc = valid_branding()
    doc["custom_domain"] = "revenue.lagosstate.gov.ng"
    assert client.put("/cp/v1/tenants/lagos/branding", json=doc,
                      headers={"X-Admin-Token": ADMIN}).status_code == 200
    resp = client.post("/cp/v1/domains/verify",
                       json={"custom_domain": "revenue.lagosstate.gov.ng"})
    assert resp.json()["tenant_state_id"] == "lagos"
    assert client.post("/cp/v1/domains/verify",
                       json={"custom_domain": "sos.lagosstate.gov.ng"}).status_code == 404
