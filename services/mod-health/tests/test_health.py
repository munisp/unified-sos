"""Tests for mod-health: billing accounts, invoices, claims, pharmacy hook."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _account(client: TestClient, facility: str = "fth-lafia") -> str:
    resp = client.post("/health/v1/billing-accounts", json={
        "tenant_state_id": "nasarawa", "facility_id": facility,
        "patient_ref": "Patient/123",
    })
    assert resp.status_code == 201
    return resp.json()["account_id"]


def test_open_billing_account(client: TestClient) -> None:
    acct_id = _account(client)
    assert acct_id.startswith("hba-")


def test_issue_and_pay_invoice(client: TestClient) -> None:
    acct_id = _account(client)
    resp = client.post("/health/v1/invoices", json={
        "account_id": acct_id,
        "lines": [
            {"service_code": "CONSULT", "quantity": 1, "unit_amount_kobo": 500000},
            {"service_code": "LAB_PANEL", "quantity": 2, "unit_amount_kobo": 150000},
        ],
    })
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert inv["status"] == "issued"
    assert inv["ledger_transfer_code"] == 150
    paid = client.post(f"/health/v1/invoices/{inv['invoice_id']}/pay")
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    # Double payment rejected.
    assert client.post(f"/health/v1/invoices/{inv['invoice_id']}/pay").status_code == 409


def test_invoice_unknown_account_404(client: TestClient) -> None:
    resp = client.post("/health/v1/invoices", json={
        "account_id": "hba-ghost",
        "lines": [{"service_code": "CONSULT", "unit_amount_kobo": 100}],
    })
    assert resp.status_code == 404


def test_claim_lifecycle(client: TestClient) -> None:
    acct_id = _account(client)
    inv = client.post("/health/v1/invoices", json={
        "account_id": acct_id,
        "lines": [{"service_code": "SURG_MINOR", "unit_amount_kobo": 2500000}],
    }).json()
    claim = client.post("/health/v1/claims",
                        json={"invoice_id": inv["invoice_id"], "payer": "SHIA"})
    assert claim.status_code == 201
    cid = claim.json()["claim_id"]
    assert claim.json()["status"] == "submitted"
    assert claim.json()["amount_kobo"] == 2500000

    # Cannot settle before adjudication.
    assert client.post(f"/health/v1/claims/{cid}/settle").status_code == 409
    adj = client.post(f"/health/v1/claims/{cid}/adjudicate",
                      json={"approve": True, "note": "verified < 24h"})
    assert adj.status_code == 200 and adj.json()["status"] == "adjudicated"
    # Cannot re-adjudicate.
    assert client.post(f"/health/v1/claims/{cid}/adjudicate",
                       json={"approve": True}).status_code == 409
    settled = client.post(f"/health/v1/claims/{cid}/settle")
    assert settled.status_code == 200 and settled.json()["status"] == "paid"


def test_claim_rejection_path(client: TestClient) -> None:
    acct_id = _account(client)
    inv = client.post("/health/v1/invoices", json={
        "account_id": acct_id,
        "lines": [{"service_code": "CONSULT", "unit_amount_kobo": 100}],
    }).json()
    cid = client.post("/health/v1/claims",
                      json={"invoice_id": inv["invoice_id"], "payer": "NHIS"}).json()["claim_id"]
    rej = client.post(f"/health/v1/claims/{cid}/adjudicate",
                      json={"approve": False, "note": "duplicate"})
    assert rej.json()["status"] == "rejected"
    assert client.post(f"/health/v1/claims/{cid}/settle").status_code == 409


def test_pharmacy_stock_out_hook(client: TestClient) -> None:
    acct_id = _account(client)
    # Stock 5 units of AMOX500 at the facility.
    stock = client.post("/health/v1/pharmacy/stock", json={
        "facility_id": "fth-lafia", "drug_code": "AMOX500", "quantity": 5,
    })
    assert stock.json()["available_units"] == 5

    # Billing 3 units succeeds and reserves stock.
    ok = client.post("/health/v1/invoices", json={
        "account_id": acct_id,
        "lines": [{"service_code": "DRUG:AMOX500", "quantity": 3, "unit_amount_kobo": 25000}],
    })
    assert ok.status_code == 201
    lvl = client.get("/health/v1/pharmacy/stock/fth-lafia/AMOX500").json()
    assert lvl["available_units"] == 2

    # Stock-out: requesting 5 more than the 2 remaining → 409 with detail.
    out = client.post("/health/v1/invoices", json={
        "account_id": acct_id,
        "lines": [{"service_code": "DRUG:AMOX500", "quantity": 5, "unit_amount_kobo": 25000}],
    })
    assert out.status_code == 409
    detail = out.json()["detail"]
    assert detail["error"] == "stock_out"
    assert detail["available"] == 2 and detail["requested"] == 5
    # Failed issuance did not consume stock.
    assert client.get("/health/v1/pharmacy/stock/fth-lafia/AMOX500").json()["available_units"] == 2


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
