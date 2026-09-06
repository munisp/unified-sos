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
