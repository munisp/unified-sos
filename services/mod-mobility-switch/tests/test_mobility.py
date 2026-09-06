"""Tests for mod-mobility-switch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

FARE_TABLE = {
    "gazette_reference": "LAMATA-HARMONIZATION-2026-03",
    "union_commission_pct": 5.0,
    "fares": [
        {"mode": "bus", "route": "BRT-IKORODU-CMS", "fare_kobo": 50000},
        {"mode": "rail", "route": "BLUE-LINE", "fare_kobo": 75000},
        {"mode": "ferry", "route": "*", "fare_kobo": 100000},
    ],
}


@pytest.fixture()
def client() -> TestClient:
    c = TestClient(create_app())
    resp = c.put("/mobility/v1/fares/lagos", json=FARE_TABLE)
    assert resp.status_code == 200
    return c


def _tap(client: TestClient, mode: str, route: str, operator: str = "lbsl",
         card: str = "cowry-00aa") -> dict:
    resp = client.post("/mobility/v1/clearing", json={
        "tenant_state_id": "lagos", "operator_id": operator,
        "mode": mode, "route": route, "card_ref": card,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_fare_table_config_and_band(client: TestClient) -> None:
    table = client.get("/mobility/v1/fares/lagos").json()
    assert table["gazette_reference"] == "LAMATA-HARMONIZATION-2026-03"
    out_of_band = client.put("/mobility/v1/fares/ogun", json={
        **FARE_TABLE, "union_commission_pct": 9.0,
    })
    assert out_of_band.status_code == 422
    assert client.get("/mobility/v1/fares/ogun").status_code == 404


def test_tap_clearing_priced_from_table(client: TestClient) -> None:
    rec = _tap(client, "bus", "BRT-IKORODU-CMS")
    assert rec["fare_kobo"] == 50000
    rail = _tap(client, "rail", "BLUE-LINE")
    assert rail["fare_kobo"] == 75000
    wildcard = _tap(client, "ferry", "IKOYI-VI")
    assert wildcard["fare_kobo"] == 100000
    unknown = client.post("/mobility/v1/clearing", json={
        "tenant_state_id": "lagos", "operator_id": "lbsl",
        "mode": "bus", "route": "NO-SUCH-ROUTE", "card_ref": "cowry-00aa",
    })
    assert unknown.status_code == 422


def test_settlement_batch_split_legs(client: TestClient) -> None:
    _tap(client, "bus", "BRT-IKORODU-CMS")            # 50_000
    _tap(client, "rail", "BLUE-LINE", card="cowry-00ab")  # 75_000
    resp = client.post("/mobility/v1/settlements/lagos/lbsl")
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["gross_kobo"] == 125000
    assert batch["record_count"] == 2
    legs = {l["beneficiary"]: l for l in batch["legs"]}
    union = legs["TRANSPORT_UNION_COMMISSION"]
    assert union["tigerbeetle_account_code"] == 4002
    assert union["amount_kobo"] == round(125000 * 0.05)
    assert union["transfer_code"] == 140
    assert legs["STATE_CONSOLIDATED_REVENUE_FUND"]["tigerbeetle_account_code"] == 3001
    # Legs sum to gross.
    assert sum(l["amount_kobo"] for l in batch["legs"]) == batch["gross_kobo"]
    # Second settlement with nothing pending → 409.
    assert client.post("/mobility/v1/settlements/lagos/lbsl").status_code == 409


def test_cowry_bridge_stub(client: TestClient) -> None:
    ok = client.post("/mobility/v1/cowry/authorize",
                     json={"card_ref": "cowry-00aa", "fare_kobo": 50000})
    assert ok.json()["decision"] == "APPROVED"
    assert ok.json()["bridge"] == "cowry-gen2-stub"
    declined = client.post("/mobility/v1/cowry/authorize",
                           json={"card_ref": "cowry-00ab", "fare_kobo": 50000})
    assert declined.json()["decision"] == "DECLINED"
