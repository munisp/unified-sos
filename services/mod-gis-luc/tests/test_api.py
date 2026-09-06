"""API tests for the LUC valuation endpoints (runs, bills, tenancy scoping)."""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from luc_app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _run_body() -> dict:
    return {
        "assessment_year": 2026,
        "parcels": [
            {"parcel_uin": "OG-ABK-0001", "owner_stin": "STIN-1",
             "land_use_type": "RESIDENTIAL", "area_sqm": 1000.0},
            {"parcel_uin": "OG-ABK-0002", "owner_stin": "STIN-2",
             "land_use_type": "COMMERCIAL", "area_sqm": 500.0,
             "relief_codes": ["OWNER_OCCUPIER_RESIDENTIAL"]},
        ],
        "findings": [
            {"building_footprint_id": "BF-1", "estimated_area_sqm": 120.0,
             "parcel_uin": "OG-ABK-0001", "owner_stin": "STIN-1",
             "assessed_annual_luc_kobo": 120000, "audit_status": "COMPLIANT"},
            {"building_footprint_id": "BF-2", "estimated_area_sqm": 300.0,
             "audit_status": "UNREGISTERED_ENCROACHMENT"},
        ],
    }


class TestValuationRuns:
    def test_trigger_run_and_fetch_bills(self, client):
        resp = client.post("/api/v1/states/ogun/luc/valuation-runs", json=_run_body())
        assert resp.status_code == 201, resp.text
        summary = resp.json()
        # OG-ABK-0001 compliant via finding; OG-ABK-0002 billed recurring;
        # one provisional encroachment bill.
        assert summary["bills_generated"] == 2
        assert summary["encroachment_provisional_bills"] == 1

        bills = client.get("/api/v1/states/ogun/luc/bills").json()
        assert len(bills) == 2
        by_id = client.get(f"/api/v1/states/ogun/luc/bills/{bills[0]['bill_id']}").json()
        assert by_id["bill_id"] == bills[0]["bill_id"]

    def test_bills_filterable_by_parcel(self, client):
        client.post("/api/v1/states/ogun/luc/valuation-runs", json=_run_body())
        bills = client.get("/api/v1/states/ogun/luc/bills", params={"parcel_uin": "OG-ABK-0002"}).json()
        assert len(bills) == 1
        assert bills[0]["relief_fraction"] == 0.25
        # gross = max(500 m² × 400 kobo, ₦5,000 floor) = 500k; net = gross × 0.75.
        assert bills[0]["net_amount_kobo"] == round(500_000 * 0.75)

    def test_runs_listed_per_state(self, client):
        client.post("/api/v1/states/ogun/luc/valuation-runs", json=_run_body())
        runs = client.get("/api/v1/states/ogun/luc/valuation-runs").json()
        assert len(runs) == 1

    def test_undeployed_state_404(self, client):
        resp = client.post("/api/v1/states/taraba/luc/valuation-runs", json=_run_body())
        assert resp.status_code == 404
        assert client.get("/api/v1/states/taraba/luc/bills").status_code == 404

    def test_tenancy_isolation_of_bills(self, client):
        client.post("/api/v1/states/ogun/luc/valuation-runs", json=_run_body())
        assert client.get("/api/v1/states/lagos/luc/bills").json() == []
        ogun_bill = client.get("/api/v1/states/ogun/luc/bills").json()[0]
        # An Ogun bill id must not be fetchable under another tenant.
        assert client.get(f"/api/v1/states/lagos/luc/bills/{ogun_bill['bill_id']}").status_code == 404
