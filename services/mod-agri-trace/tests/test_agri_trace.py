"""Tests for mod-agri-trace — tenancy, WR lifecycle, hash chains, trace,
fail-closed adapters, fixture determinism."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _shared.eventbus import InMemoryEventBus
from _shared.hashchain import GENESIS_PREV_HASH, verify_event_chain
from app.adapters import (
    AdapterUnavailableError,
    FixtureCommodityExchange,
    FixtureWarehouseIoT,
    build_exchange_adapter,
    build_iot_adapter,
)
from app.domain import (
    EVENT_LOT_TRACED_HOP,
    EVENT_RECEIPT_ISSUED,
    EVENT_RECEIPT_PLEDGED,
    EVENT_RECEIPT_REDEEMED,
    AgriStore,
    InvalidTransitionError,
    catalog_for,
)
from app.main import create_app

BENUE = {"X-State-Tenant": "benue"}
OSUN = {"X-State-Tenant": "osun"}

LOT = {
    "commodity": "yam",
    "weight_kg": 1250.0,
    "grade": "A",
    "moisture_pct": 12.5,
    "latitude": 7.73,   # Makurdi, Benue
    "longitude": 8.53,
}


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def client(bus) -> TestClient:
    return TestClient(create_app(bus=bus))


def _farmer(client: TestClient, headers=BENUE, name="Aondofa K.", kyc="KYC-001") -> str:
    resp = client.post("/agri/v1/farmers",
                       json={"name": name, "kyc_ref": kyc}, headers=headers)
    assert resp.status_code == 201
    return resp.json()["farmer_id"]


def _lot(client: TestClient, headers=BENUE, farmer_id=None, **overrides) -> str:
    farmer_id = farmer_id or _farmer(client, headers)
    body = {**LOT, "farmer_id": farmer_id, **overrides}
    resp = client.post("/agri/v1/lots", json=body, headers=headers)
    assert resp.status_code == 201
    return resp.json()["lot_id"]


def _warehouse(client: TestClient, headers=BENUE) -> str:
    resp = client.post("/agri/v1/warehouses", json={
        "name": "Makurdi Agro-Hub 01", "lga": "Makurdi", "capacity_kg": 500000,
        "latitude": 7.74, "longitude": 8.52,
    }, headers=headers)
    assert resp.status_code == 201
    return resp.json()["warehouse_id"]


def _receipt(client: TestClient, headers=BENUE) -> str:
    lot_id = _lot(client, headers)
    wh = _warehouse(client, headers)
    resp = client.post("/agri/v1/receipts", json={
        "warehouse_id": wh, "lot_id": lot_id, "storage_fees_kobo": 150000,
    }, headers=headers)
    assert resp.status_code == 201
    return resp.json()["receipt_id"]


# --- tenancy -------------------------------------------------------------------

def test_tenant_header_required(client: TestClient) -> None:
    assert client.get("/agri/v1/farmers").status_code == 400
    assert client.post("/agri/v1/farmers", json={"name": "x", "kyc_ref": "k"}).status_code == 400


def test_tenant_isolation_farmers(client: TestClient) -> None:
    fid = _farmer(client, BENUE)
    assert [f["farmer_id"] for f in client.get("/agri/v1/farmers", headers=BENUE).json()] == [fid]
    assert client.get("/agri/v1/farmers", headers=OSUN).json() == []
    # a benue farmer id does not resolve under the osun tenant
    assert client.get(f"/agri/v1/farmers/{fid}", headers=OSUN).status_code == 404


def test_tenant_isolation_lots_and_receipts(client: TestClient) -> None:
    rid = _receipt(client, BENUE)
    assert client.get("/agri/v1/lots", headers=OSUN).json() == []
    assert client.get("/agri/v1/receipts", headers=OSUN).json() == []
    assert client.get(f"/agri/v1/receipts/{rid}", headers=OSUN).status_code == 404


# --- commodity catalog -----------------------------------------------------------

def test_catalog_fixtures(client: TestClient) -> None:
    assert client.get("/agri/v1/commodities", headers=BENUE).json()["commodities"] == ["yam"]
    assert client.get("/agri/v1/commodities", headers=OSUN).json()["commodities"] == ["cocoa"]
    assert client.get("/agri/v1/commodities", headers={"X-State-Tenant": "kano"}).json()["commodities"] == ["sorghum", "maize"]
    assert client.get("/agri/v1/commodities", headers={"X-State-Tenant": "taraba"}).json()["commodities"] == ["tea"]
    assert client.get("/agri/v1/commodities", headers={"X-State-Tenant": "kebbi"}).json()["commodities"] == ["rice"]


def test_catalog_generic_fallback() -> None:
    assert catalog_for("lagos") == ["maize", "cassava", "soybeans", "millet", "groundnut"]


# --- intake & geo validation ------------------------------------------------------

def test_lot_intake_records_farm_hop(client: TestClient) -> None:
    lot_id = _lot(client)
    trace = client.get(f"/agri/v1/trace/{lot_id}", headers=BENUE).json()
    assert trace["chain_valid"] is True
    assert [h["stage"] for h in trace["hops"]] == ["farm"]


def test_lot_intake_geo_outside_nigeria_rejected(client: TestClient) -> None:
    fid = _farmer(client)
    resp = client.post("/agri/v1/lots", json={
        **LOT, "farmer_id": fid, "latitude": 51.5, "longitude": -0.12,
    }, headers=BENUE)
    assert resp.status_code == 422


def test_lot_intake_unknown_farmer(client: TestClient) -> None:
    resp = client.post("/agri/v1/lots", json={**LOT, "farmer_id": "farmer-nope"},
                       headers=BENUE)
    assert resp.status_code == 404


# --- warehouse receipts -------------------------------------------------------------

def test_receipt_issue_signed_and_evented(client: TestClient, bus: InMemoryEventBus) -> None:
    rid = _receipt(client)
    receipt = client.get(f"/agri/v1/receipts/{rid}", headers=BENUE).json()
    assert receipt["status"] == "issued"
    assert receipt["quantity_kg"] == LOT["weight_kg"]
    assert receipt["storage_fees_kobo"] == 150000
    # lot now has farm + warehouse hops
    topics = [e["topic"] for e in bus.published]
    assert EVENT_RECEIPT_ISSUED in topics
    assert topics.count(EVENT_LOT_TRACED_HOP) == 2


def test_double_issue_same_lot_conflict(client: TestClient) -> None:
    lot_id = _lot(client)
    wh = _warehouse(client)
    body = {"warehouse_id": wh, "lot_id": lot_id, "storage_fees_kobo": 1000}
    assert client.post("/agri/v1/receipts", json=body, headers=BENUE).status_code == 201
    assert client.post("/agri/v1/receipts", json=body, headers=BENUE).status_code == 409


def test_full_lifecycle_issue_pledge_release_redeem(client: TestClient,
                                                    bus: InMemoryEventBus) -> None:
    rid = _receipt(client)
    r = client.post(f"/agri/v1/receipts/{rid}/pledge",
                    json={"pledgee_ref": "bank-abc"}, headers=BENUE)
    assert r.status_code == 200 and r.json()["status"] == "pledged"
    assert r.json()["pledgee_ref"] == "bank-abc"
    r = client.post(f"/agri/v1/receipts/{rid}/release", headers=BENUE)
    assert r.json()["status"] == "released"
    r = client.post(f"/agri/v1/receipts/{rid}/redeem", headers=BENUE)
    assert r.json()["status"] == "redeemed"
    topics = [e["topic"] for e in bus.published]
    assert EVENT_RECEIPT_PLEDGED in topics
    assert topics.count(EVENT_RECEIPT_REDEEMED) == 2


def test_redeem_directly_from_issued(client: TestClient) -> None:
    rid = _receipt(client)
    r = client.post(f"/agri/v1/receipts/{rid}/redeem", headers=BENUE)
    assert r.status_code == 200 and r.json()["status"] == "redeemed"


@pytest.mark.parametrize("action,body", [
    ("release", None),
    ("transfer", {"from_holder": "x", "to_holder": "y"}),
])
def test_invalid_transitions_from_issued(client: TestClient, action, body) -> None:
    rid = _receipt(client)
    resp = client.post(f"/agri/v1/receipts/{rid}/{action}", json=body, headers=BENUE)
    assert resp.status_code == 409


def test_pledge_redeemed_receipt_conflict(client: TestClient) -> None:
    rid = _receipt(client)
    client.post(f"/agri/v1/receipts/{rid}/redeem", headers=BENUE)
    resp = client.post(f"/agri/v1/receipts/{rid}/pledge",
                       json={"pledgee_ref": "bank-abc"}, headers=BENUE)
    assert resp.status_code == 409


def test_lifecycle_unknown_receipt_404(client: TestClient) -> None:
    assert client.post("/agri/v1/receipts/wr-nope/redeem", headers=BENUE).status_code == 404


# --- title transfer (double entry) -----------------------------------------------------

def test_title_transfer_double_entry(client: TestClient) -> None:
    rid = _receipt(client)
    holder = client.get(f"/agri/v1/receipts/{rid}", headers=BENUE).json()["holder_id"]
    r = client.post(f"/agri/v1/receipts/{rid}/transfer",
                    json={"from_holder": holder, "to_holder": "buyer-001"},
                    headers=BENUE)
    assert r.status_code == 200 and r.json()["holder_id"] == "buyer-001"
    ledger = client.get(f"/agri/v1/receipts/{rid}/ledger", headers=BENUE).json()
    entries = ledger["entries"]
    assert ledger["ledger_valid"] is True
    assert [e["entry_type"] for e in entries] == ["debit", "credit"]
    assert entries[0]["holder_id"] == holder and entries[1]["holder_id"] == "buyer-001"
    assert entries[0]["quantity_kg"] == entries[1]["quantity_kg"]


def test_title_transfer_wrong_holder_conflict(client: TestClient) -> None:
    rid = _receipt(client)
    resp = client.post(f"/agri/v1/receipts/{rid}/transfer",
                       json={"from_holder": "someone-else", "to_holder": "buyer-001"},
                       headers=BENUE)
    assert resp.status_code == 409


def test_title_transfer_after_redeem_conflict(client: TestClient) -> None:
    rid = _receipt(client)
    holder = client.get(f"/agri/v1/receipts/{rid}", headers=BENUE).json()["holder_id"]
    client.post(f"/agri/v1/receipts/{rid}/redeem", headers=BENUE)
    resp = client.post(f"/agri/v1/receipts/{rid}/transfer",
                       json={"from_holder": holder, "to_holder": "buyer-001"},
                       headers=BENUE)
    assert resp.status_code == 409


# --- hash-chain integrity ---------------------------------------------------------------

def test_receipt_chain_integrity(client: TestClient) -> None:
    rid = _receipt(client)
    client.post(f"/agri/v1/receipts/{rid}/pledge", json={"pledgee_ref": "b"}, headers=BENUE)
    result = client.get("/agri/v1/receipt-chain/verify", headers=BENUE).json()
    assert result == {"tenant_state_id": "benue", "chain_valid": True, "errors": []}


def test_receipt_chain_tamper_detected(client: TestClient) -> None:
    store = AgriStore()
    tampered_client = TestClient(create_app(store=store))
    rid = _receipt(tampered_client)
    # tamper with the recorded quantity in the genesis event
    store.tenant("benue").receipt_chain[0]["quantity_kg"] = 1.0
    result = tampered_client.get("/agri/v1/receipt-chain/verify", headers=BENUE).json()
    assert result["chain_valid"] is False
    assert any("tampered" in e or "broken chain" in e for e in result["errors"])


def test_trace_tamper_detected(client: TestClient) -> None:
    store = AgriStore()
    tampered_client = TestClient(create_app(store=store))
    lot_id = _lot(tampered_client)
    store.tenant("benue").trace_hops[lot_id][0].actor = "evil-actor"
    trace = tampered_client.get(f"/agri/v1/trace/{lot_id}", headers=BENUE).json()
    assert trace["chain_valid"] is False
    assert trace["errors"]


# --- traceability ------------------------------------------------------------------------

def test_full_trace_chain_assembly(client: TestClient) -> None:
    lot_id = _lot(client)
    wh = _warehouse(client)
    client.post("/agri/v1/trace/hops", json={
        "lot_id": lot_id, "stage": "aggregation_center", "actor": "agg-makurdi",
        "latitude": 7.74, "longitude": 8.52, "note": "graded & bagged",
    }, headers=BENUE)
    client.post("/agri/v1/receipts",
                json={"warehouse_id": wh, "lot_id": lot_id, "storage_fees_kobo": 5000},
                headers=BENUE)
    client.post("/agri/v1/trace/hops", json={
        "lot_id": lot_id, "stage": "processor", "actor": "proc-ltd",
        "latitude": 7.75, "longitude": 8.54,
    }, headers=BENUE)
    trace = client.get(f"/agri/v1/trace/{lot_id}", headers=BENUE).json()
    assert trace["chain_valid"] is True
    assert [h["stage"] for h in trace["hops"]] == [
        "farm", "aggregation_center", "warehouse", "processor",
    ]
    # hop hashes are linked in order
    hops = trace["hops"]
    assert hops[0]["prev_hash"] == GENESIS_PREV_HASH
    for prev, cur in zip(hops, hops[1:]):
        assert cur["prev_hash"] == prev["event_hash"]
    assert verify_event_chain(hops) == []


def test_trace_hop_geo_validated(client: TestClient) -> None:
    lot_id = _lot(client)
    resp = client.post("/agri/v1/trace/hops", json={
        "lot_id": lot_id, "stage": "export", "actor": "exporter",
        "latitude": 0.0, "longitude": 0.0,
    }, headers=BENUE)
    assert resp.status_code == 422


def test_trace_unknown_lot_404(client: TestClient) -> None:
    assert client.get("/agri/v1/trace/lot-nope", headers=BENUE).status_code == 404


# --- adapters -------------------------------------------------------------------------------

def test_fixture_price_determinism() -> None:
    ex = FixtureCommodityExchange()
    q1 = ex.get_price("benue", "yam")
    q2 = ex.get_price("benue", "YAM")
    assert q1 == q2
    assert q1.price_kobo_per_kg == 62000
    assert q1.currency == "NGN"
    with pytest.raises(KeyError):
        ex.get_price("benue", "unobtanium")


def test_exchange_adapter_fail_closed_production() -> None:
    with pytest.raises(AdapterUnavailableError):
        build_exchange_adapter({"SOS_AGRI_PROFILE": "production"})
    with pytest.raises(AdapterUnavailableError):
        build_exchange_adapter({"SOS_AGRI_PROFILE": "live"})


def test_iot_adapter_fail_closed_production() -> None:
    with pytest.raises(AdapterUnavailableError):
        build_iot_adapter({"SOS_AGRI_PROFILE": "production"})


def test_unknown_profile_fail_closed() -> None:
    with pytest.raises(AdapterUnavailableError):
        build_exchange_adapter({"SOS_AGRI_PROFILE": "mystery"})


def test_production_profile_with_config_builds() -> None:
    ex = build_exchange_adapter({"SOS_AGRI_PROFILE": "production",
                                 "SOS_AGRI_EXCHANGE_URL": "https://afex.example"})
    iot = build_iot_adapter({"SOS_AGRI_PROFILE": "production",
                             "SOS_AGRI_IOT_URL": "https://iot.example"})
    assert ex.health() is False  # no live backend in tests, but constructible
    assert iot is not None


def test_price_endpoint(client: TestClient) -> None:
    resp = client.get("/agri/v1/prices/yam", headers=BENUE)
    assert resp.status_code == 200
    assert resp.json()["price_kobo_per_kg"] == 62000
    assert client.get("/agri/v1/prices/nope", headers=BENUE).status_code == 404


def test_telemetry_ingest_and_latest(client: TestClient) -> None:
    wh = _warehouse(client)
    resp = client.post("/agri/v1/telemetry", json={
        "warehouse_id": wh, "moisture_pct": 11.0, "temperature_c": 27.5,
    }, headers=BENUE)
    assert resp.status_code == 201
    readings = client.get(f"/agri/v1/telemetry/{wh}", headers=BENUE).json()
    assert len(readings) == 1 and readings[0]["moisture_pct"] == 11.0
    # telemetry is tenant-scoped
    assert client.get(f"/agri/v1/telemetry/{wh}", headers=OSUN).json() == []


def test_telemetry_unknown_warehouse(client: TestClient) -> None:
    resp = client.post("/agri/v1/telemetry", json={
        "warehouse_id": "wh-nope", "moisture_pct": 10, "temperature_c": 25,
    }, headers=BENUE)
    assert resp.status_code == 404


def test_fixture_iot_deterministic() -> None:
    iot = FixtureWarehouseIoT()
    r1 = iot.ingest("benue", "wh-1", 10.0, 25.0)
    r2 = iot.ingest("benue", "wh-1", 11.0, 26.0)
    assert r1.reading_id != r2.reading_id
    assert r1.recorded_at == r2.recorded_at  # fixed fixture timestamp


# --- platform ---------------------------------------------------------------------------------

def test_healthz_and_metrics(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    _receipt(client)
    metrics = client.get("/metrics").text
    assert "service_info" in metrics
    assert "agri_warehouse_receipts_total 1" in metrics
    assert "agri_lots_total 1" in metrics
