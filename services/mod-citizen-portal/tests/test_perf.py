"""Perf tests for mod-citizen-portal (perf-internal assertions only).

Covers: audit appends derive ``seq`` via O(1) ``audit_count()``, the app
reuses one service instance (and cached channel adapters) across requests,
and catalog seeding/wallet creation stay fast under iteration.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import InMemoryCitizenPortalRepository
from app.service import CitizenPortalService

RAW_NIN = "12345678901"


def test_audit_append_uses_o1_count_not_list_copy():
    repo = InMemoryCitizenPortalRepository()
    svc = CitizenPortalService(repo)
    calls = []
    orig = repo.list_audit

    def counting_list_audit(state_id=None):
        calls.append(state_id)
        return orig(state_id)

    repo.list_audit = counting_list_audit
    for i in range(50):
        svc.register_channel_pin("lagos", f"msisdn-hash-{i}", "1234")
    assert calls == []
    assert repo.audit_count() == len(orig())


def test_app_reuses_service_and_channel_adapters():
    app = create_app(InMemoryCitizenPortalRepository())
    client = TestClient(app)
    resp = client.post(
        "/citizen/v1/wallets", json={"state_id": "lagos", "nin": RAW_NIN}
    )
    assert resp.status_code == 201
    first = app.state.service_instance
    wallet_id = resp.json()["wallet_id"]
    assert client.get(
        f"/citizen/v1/wallets/{wallet_id}", params={"state_id": "lagos"}
    ).status_code == 200
    assert app.state.service_instance is first

    # USSD callback reuses a cached adapter (prompt table copied once).
    import os

    os.environ["CITIZEN_PORTAL_TELCO_SECRET"] = "s3cret"
    try:
        cb = {
            "sessionId": "sess-1",
            "phoneNumber": "2348012345678",
            "text": "",
            "seq": "1",
            "sessionToken": "tok-1",
        }
        r1 = client.post(
            "/channels/ussd/callback",
            params={"state_id": "lagos"},
            data=cb,
            headers={"x-telco-secret": "s3cret"},
        )
        assert r1.status_code == 200
        cache = first._adapter_cache
        assert ("ussd", "") in cache
        cb["seq"] = "2"
        r2 = client.post(
            "/channels/ussd/callback",
            params={"state_id": "lagos"},
            data=cb,
            headers={"x-telco-secret": "s3cret"},
        )
        assert r2.status_code == 200
        assert first._adapter_cache[("ussd", "")] is cache[("ussd", "")]
    finally:
        del os.environ["CITIZEN_PORTAL_TELCO_SECRET"]


def test_catalog_and_wallet_hot_path_smoke():
    svc = CitizenPortalService(InMemoryCitizenPortalRepository())
    svc.ensure_catalog("lagos")
    start = time.perf_counter()
    for i in range(300):
        svc.ensure_catalog("lagos")  # seeded: pure read path
        svc.create_wallet("lagos", f"nin-{i}")
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0
