"""Tests for mod-education: billing portal, registration lock, Mojaloop webhook."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _student(client: TestClient) -> str:
    resp = client.post("/education/v1/students", json={
        "tenant_state_id": "osun", "institution_id": "UNIOSUN",
        "matric_no": "UNI/2026/0001",
    })
    assert resp.status_code == 201
    return resp.json()["student_id"]


def _invoice(client: TestClient, student_id: str, amount: int = 35000000) -> dict:
    resp = client.post("/education/v1/invoices", json={
        "student_id": student_id, "session": "2026/2027",
        "lines": [
            {"fee_type": "TUITION", "amount_kobo": amount - 500000},
            {"fee_type": "ACCOMMODATION", "amount_kobo": 500000},
        ],
    })
    assert resp.status_code == 201
    return resp.json()


def test_registration_unlocked_with_no_outstanding(client: TestClient) -> None:
    sid = _student(client)
    resp = client.post("/education/v1/registrations", json={
        "student_id": sid, "session": "2026/2027", "courses": ["CSC301", "MTH301"],
    })
    assert resp.status_code == 201


def test_registration_lock_tied_to_payment_status(client: TestClient) -> None:
    sid = _student(client)
    inv = _invoice(client, sid)

    portal = client.get(f"/education/v1/students/{sid}/billing").json()
    assert portal["registration_locked"] is True
    assert portal["outstanding_kobo"] == 35000000

    locked = client.post("/education/v1/registrations", json={
        "student_id": sid, "session": "2026/2027", "courses": ["CSC301"],
    })
    assert locked.status_code == 423
    assert "locked" in locked.json()["detail"]

    # Pay via portal → lock clears in real time.
    assert client.post(f"/education/v1/invoices/{inv['invoice_id']}/pay").status_code == 200
    portal = client.get(f"/education/v1/students/{sid}/billing").json()
    assert portal["registration_locked"] is False
    ok = client.post("/education/v1/registrations", json={
        "student_id": sid, "session": "2026/2027", "courses": ["CSC301"],
    })
    assert ok.status_code == 201


def test_mojaloop_webhook_clears_invoice(client: TestClient) -> None:
    sid = _student(client)
    inv = _invoice(client, sid, amount=1000000)
    resp = client.post("/education/v1/webhooks/mojaloop", json={
        "transfer_id": "mjl-tx-777", "invoice_id": inv["invoice_id"],
        "amount_kobo": 1000000,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"
    assert resp.json()["ledger_transfer_code"] == 150


def test_mojaloop_webhook_underpayment_rejected(client: TestClient) -> None:
    sid = _student(client)
    inv = _invoice(client, sid, amount=1000000)
    resp = client.post("/education/v1/webhooks/mojaloop", json={
        "transfer_id": "mjl-tx-778", "invoice_id": inv["invoice_id"],
        "amount_kobo": 999999,
    })
    assert resp.status_code == 422
    # Invoice remains unpaid → still locked.
    assert client.get(f"/education/v1/students/{sid}/billing").json()["registration_locked"]


def test_404s(client: TestClient) -> None:
    assert client.get("/education/v1/students/stu-ghost/billing").status_code == 404
    assert client.post("/education/v1/invoices/stu-ghost/pay").status_code == 404
    assert client.post("/education/v1/registrations", json={
        "student_id": "stu-ghost", "session": "s", "courses": ["X"],
    }).status_code == 404


# --- FSPIOP fulfilment mode (adapter wired) ------------------------------------

import hashlib
import hmac
import json

_TEST_SECRET = "test-callback-secret"


class _FakeFspiopVerifier:
    """Duck-typed stand-in for FspiopAdapter (HMAC sim profile)."""

    def verify_inbound_signature(self, headers, body: bytes) -> bool:
        sig = headers.get("fspiop-signature", "")
        expected = "sha256=" + hmac.new(
            _TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)


def _signed_fulfilment(client: TestClient, invoice_id: str, amount: int,
                       transfer_id: str = "mjl-tx-900", state: str = "COMMITTED",
                       sign: bool = True):
    body = json.dumps({
        "transfer_id": transfer_id, "invoice_id": invoice_id,
        "amount_kobo": amount, "transfer_state": state,
        "fulfilment": "Zml4dHVyZQ==", "completed_timestamp": "2026-01-01T00:00:00Z",
    }, sort_keys=True).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["FSPIOP-Signature"] = "sha256=" + hmac.new(
            _TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/education/v1/webhooks/mojaloop", content=body,
                       headers=headers)


@pytest.fixture()
def adapter_client() -> TestClient:
    return TestClient(create_app(fspiop=_FakeFspiopVerifier()))


def test_fulfilment_mode_credits_committed_transfer(adapter_client: TestClient) -> None:
    sid = _student(adapter_client)
    inv = _invoice(adapter_client, sid, amount=1000000)
    resp = _signed_fulfilment(adapter_client, inv["invoice_id"], 1000000)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"


def test_fulfilment_mode_rejects_unsigned_callback(adapter_client: TestClient) -> None:
    sid = _student(adapter_client)
    inv = _invoice(adapter_client, sid, amount=1000000)
    resp = _signed_fulfilment(adapter_client, inv["invoice_id"], 1000000,
                              sign=False)
    assert resp.status_code == 401
    assert adapter_client.get(
        f"/education/v1/students/{sid}/billing").json()["registration_locked"]


def test_fulfilment_mode_rejects_aborted_transfer(adapter_client: TestClient) -> None:
    sid = _student(adapter_client)
    inv = _invoice(adapter_client, sid, amount=1000000)
    resp = _signed_fulfilment(adapter_client, inv["invoice_id"], 1000000,
                              state="ABORTED")
    assert resp.status_code == 409
