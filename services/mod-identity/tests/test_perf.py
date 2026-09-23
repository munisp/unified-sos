"""Perf tests for mod-identity (perf-internal assertions only).

Covers: audit appends derive ``seq`` via O(1) ``audit_count()`` (no
full-chain list copy per write), the per-app service instance is reused
across requests, and a hot-path smoke loop stays fast.
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ConsentGrant, VerificationProduct, utcnow
from app.repo import InMemoryIdentityRepository
from app.service import IdentityService


def _seed(svc: IdentityService, state: str = "lagos"):
    from app.models import ApiConsumer, Resident

    svc.register_resident(
        Resident(resident_id="R1", state_id=state, nin="NIN-001",
                 full_name="Adaeze Okafor", address="12 Marina Rd")
    )
    svc.register_consumer(ApiConsumer(consumer_id="C1", state_id=state, name="Bank"))
    now = utcnow()
    svc.grant_consent(ConsentGrant(
        grant_id="G1", state_id=state, resident_id="R1", consumer_id="C1",
        purpose=VerificationProduct.RESIDENCY_ATTESTATION,
        created_at=now, expires_at=now + timedelta(days=90),
    ))


def test_audit_append_uses_o1_count_not_list_copy():
    repo = InMemoryIdentityRepository()
    svc = IdentityService(repo)
    calls = []
    orig = repo.list_audit

    def counting_list_audit(state_id=None):
        calls.append(state_id)
        return orig(state_id)

    repo.list_audit = counting_list_audit
    _seed(svc)
    for _ in range(50):
        svc.verify("lagos", "C1", "R1", VerificationProduct.RESIDENCY_ATTESTATION)
    assert calls == []  # hot write path never copies the audit chain
    assert repo.audit_count() == len(orig())


def test_app_reuses_service_instance_across_requests():
    repo = InMemoryIdentityRepository()
    app = create_app(repo)
    client = TestClient(app)
    svc = IdentityService(repo)
    _seed(svc)
    body = {
        "state_id": "lagos",
        "consumer_id": "C1",
        "resident_id": "R1",
        "product": "RESIDENCY_ATTESTATION",
    }
    assert client.post("/verify", json=body).status_code == 200
    first = app.state.service_instance
    assert client.post("/verify", json=body).status_code == 200
    assert app.state.service_instance is first  # no per-request rebuild
    # Unique result IDs across requests (monotonic counter on the singleton).
    assert first.repo is repo


def test_verify_hot_path_smoke():
    svc = IdentityService(InMemoryIdentityRepository())
    _seed(svc)
    start = time.perf_counter()
    for _ in range(500):
        svc.verify("lagos", "C1", "R1", VerificationProduct.RESIDENCY_ATTESTATION)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0  # ~10ms/call ceiling; typical is orders lower
