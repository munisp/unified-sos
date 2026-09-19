"""Tests for the /payments/v1/quotes + /confirm citizen payment seam.

Covers quote shape/determinism, fixture fee, FSPIOP adapter fee, tenant
scoping, confirm settlement (fixture + FSPIOP), idempotent replay,
already-settled conflict, expiry (410) and unknown quote (404).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.adapters import FixtureFspiopAdapter  # noqa: E402
from app.main import create_app  # noqa: E402

TENANT = {"X-State-Tenant": "lagos"}


def _client(fspiop=None) -> TestClient:
    return TestClient(create_app(fspiop=fspiop))


def _quote(client: TestClient, ref: str = "BILL-100", amount: int = 50000,
           headers: dict | None = None) -> dict:
    resp = client.post("/payments/v1/quotes", json={
        "bill_reference": ref, "amount_kobo": amount, "payer": "cowry-00aa",
    }, headers=headers or TENANT)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_quote_shape_and_fixture_fee() -> None:
    client = _client()
    body = _quote(client)
    assert body["quote_id"].startswith("QTE-")
    assert body["amount_kobo"] == 50000
    # Deterministic fixture fee: 1% + 1000 kobo (FixtureFspiopAdapter formula).
    assert body["fees_kobo"] == 50000 // 100 + 1000
    assert body["status"] == "PENDING"
    assert body["tenant_state_id"] == "lagos"
    datetime.fromisoformat(body["expires_at"])  # parses


def test_quote_deterministic_replay() -> None:
    client = _client()
    first = _quote(client)
    second = _quote(client)
    assert second["quote_id"] == first["quote_id"]
    assert second["expires_at"] == first["expires_at"]


def test_quote_via_fspiop_adapter_fee() -> None:
    client = _client(fspiop=FixtureFspiopAdapter())
    body = _quote(client, ref="BILL-FSPIOP")
    # FixtureFspiopAdapter.quote: deterministic 1% + ₦10.00.
    assert body["fees_kobo"] == 50000 // 100 + 1000


def test_quote_requires_tenant_header() -> None:
    client = _client()
    resp = client.post("/payments/v1/quotes", json={
        "bill_reference": "BILL-1", "amount_kobo": 1000,
    })
    assert resp.status_code == 400


def test_quote_accepts_ticket_ref_alias() -> None:
    client = _client()
    resp = client.post("/payments/v1/quotes", json={
        "ticket_ref": "TKT-9", "amount_kobo": 2000,
    }, headers=TENANT)
    assert resp.status_code == 201, resp.text
    assert resp.json()["bill_reference"] == "TKT-9"


def test_quote_requires_reference() -> None:
    client = _client()
    resp = client.post("/payments/v1/quotes", json={
        "amount_kobo": 2000,
    }, headers=TENANT)
    assert resp.status_code == 422


def test_tenant_scoping() -> None:
    client = _client()
    quote = _quote(client)
    # Same quote id under a different tenant must not resolve.
    resp = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                       headers={"X-State-Tenant": "ogun"})
    assert resp.status_code == 404


def test_confirm_settles_fixture_path() -> None:
    client = _client()
    quote = _quote(client)
    resp = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                       headers={**TENANT, "Idempotency-Key": "pay-1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["settled_amount_kobo"] == quote["amount_kobo"] + quote["fees_kobo"]
    assert body["fulfilment"]


def test_confirm_idempotent_replay_same_key() -> None:
    client = _client()
    quote = _quote(client)
    first = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                        headers={**TENANT, "Idempotency-Key": "pay-2"}).json()
    replay = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                         headers={**TENANT, "Idempotency-Key": "pay-2"})
    assert replay.status_code == 200
    assert replay.json() == first


def test_confirm_replay_different_key_conflicts() -> None:
    client = _client()
    quote = _quote(client)
    assert client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                       headers={**TENANT, "Idempotency-Key": "pay-3"}
                       ).status_code == 200
    resp = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                       headers={**TENANT, "Idempotency-Key": "pay-4"})
    assert resp.status_code == 409


def test_confirm_via_fspiop_prepare_fulfil() -> None:
    fspiop = FixtureFspiopAdapter()
    client = _client(fspiop=fspiop)
    quote = _quote(client, ref="BILL-SCHEME")
    resp = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                       headers={**TENANT, "Idempotency-Key": "pay-5"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    # Settlement went through the scheme's prepare → fulfil path.
    assert quote["quote_id"] in fspiop._prepared
    assert quote["quote_id"] in fspiop._fulfilled


def test_confirm_unknown_quote_404() -> None:
    client = _client()
    resp = client.post("/payments/v1/quotes/QTE-NOPE/confirm", headers=TENANT)
    assert resp.status_code == 404


def test_confirm_expired_quote_410() -> None:
    client = _client()
    quote = _quote(client)
    # Force expiry by rewinding the stored quote's expires_at.
    stored = client.app.state.payment_quotes["lagos"][quote["quote_id"]]
    stored["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    resp = client.post(f"/payments/v1/quotes/{quote['quote_id']}/confirm",
                       headers={**TENANT, "Idempotency-Key": "pay-6"})
    assert resp.status_code == 410
