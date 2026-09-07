"""Tests for mod-border-transit (run from the service directory)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters import (
    AdapterUnavailableError,
    FixtureRfidReaderAdapter,
    FixtureTelematicsAdapter,
    build_rfid_adapter,
    build_telematics_adapter,
)
from app.domain import (
    BorderTransitStore,
    GENESIS_PREV_HASH,
    point_in_polygon,
    _NullEventBus,
)
from app.main import create_app

TARABA = {"X-State-Tenant": "taraba"}
BORNO = {"X-State-Tenant": "borno"}


@pytest.fixture()
def bus() -> _NullEventBus:
    return _NullEventBus()


@pytest.fixture()
def store(bus) -> BorderTransitStore:
    return BorderTransitStore(bus=bus)


@pytest.fixture()
def client(store) -> TestClient:
    return TestClient(create_app(store))


def _declare(client, headers=TARABA, **overrides):
    payload = {
        "trader_ref": "trader-001",
        "rfid_tag_id": "RFID-TARABA-0001",
        "goods_description": "Sesame seed, 20MT",
        "hs_code": "120740",
        "declared_value_kobo": 5_000_000_00,  # N5,000,000 in kobo
        "origin_crossing_id": "gembu-cameroon",
        "destination_crossing_id": "ibi",
    }
    payload.update(overrides)
    resp = client.post("/border/v1/consignments", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _scan(client, consignment_id, checkpoint="gembu-cameroon", headers=TARABA,
          **overrides):
    payload = {
        "consignment_id": consignment_id,
        "checkpoint_id": checkpoint,
        "direction": "entry",
        "scanned_at": "2025-01-01T09:00:00+00:00",
        "latitude": 6.72,
        "longitude": 11.27,
        "seal_intact": True,
    }
    payload.update(overrides)
    return client.post("/border/v1/scans", json=payload, headers=headers)


# --- tenancy ----------------------------------------------------------------


def test_missing_tenant_header_is_400(client):
    assert client.get("/border/v1/crossings").status_code == 400


def test_fixture_crossings_per_state(client):
    crossings = client.get("/border/v1/crossings", headers=TARABA).json()
    names = {c["name"] for c in crossings}
    assert names == {"Gembu–Cameroon", "Ibi"}
    borno = client.get("/border/v1/crossings", headers=BORNO).json()
    assert {c["name"] for c in borno} == {"Gamboru–Chad", "Banki"}


def test_cross_tenant_consignment_access_forbidden(client):
    con = _declare(client)
    resp = client.get(f"/border/v1/consignments/{con['consignment_id']}",
                      headers=BORNO)
    assert resp.status_code == 403


def test_tenant_isolation_of_lists(client):
    _declare(client, headers=TARABA)
    assert client.get("/border/v1/consignments", headers=BORNO).json() == []
    assert len(client.get("/border/v1/consignments", headers=TARABA).json()) == 1


# --- crossings CRUD ------------------------------------------------------------


def test_crossing_crud_roundtrip(client):
    resp = client.post("/border/v1/crossings/mambilla-cameroon", json={
        "name": "Mambilla–Cameroon", "neighbor_country": "Cameroon",
        "latitude": 7.0, "longitude": 11.4,
    }, headers=TARABA)
    assert resp.status_code == 201
    resp = client.patch("/border/v1/crossings/mambilla-cameroon", json={
        "name": "Mambilla Plateau–Cameroon", "neighbor_country": "Cameroon",
        "latitude": 7.0, "longitude": 11.4,
    }, headers=TARABA)
    assert resp.json()["name"] == "Mambilla Plateau–Cameroon"
    assert client.delete("/border/v1/crossings/mambilla-cameroon",
                         headers=TARABA).status_code == 204
    assert client.get("/border/v1/crossings/mambilla-cameroon",
                      headers=TARABA).status_code == 404


def test_duplicate_crossing_conflict(client):
    body = {"name": "X", "neighbor_country": "Niger",
            "latitude": 1.0, "longitude": 1.0}
    assert client.post("/border/v1/crossings/dup-x", json=body,
                       headers=TARABA).status_code == 201
    assert client.post("/border/v1/crossings/dup-x", json=body,
                       headers=TARABA).status_code == 409


# --- lifecycle --------------------------------------------------------------------


def test_happy_path_lifecycle(client):
    con = _declare(client)
    cid = con["consignment_id"]
    assert con["state"] == "declared"
    assert client.post(f"/border/v1/consignments/{cid}/seal",
                       headers=TARABA).json()["state"] == "sealed"
    _scan(client, cid)  # first scan auto-advances to in_transit
    assert client.get(f"/border/v1/consignments/{cid}",
                      headers=TARABA).json()["state"] == "in_transit"
    _scan(client, cid, checkpoint="ibi", direction="exit")
    assert client.post(f"/border/v1/consignments/{cid}/arrive",
                       headers=TARABA).json()["state"] == "arrived"
    resp = client.post(f"/border/v1/consignments/{cid}/clear",
                       json={"officer_ref": "off-7"}, headers=TARABA)
    assert resp.status_code == 200
    assert client.get(f"/border/v1/consignments/{cid}",
                      headers=TARABA).json()["state"] == "cleared"


def test_invalid_transition_declared_to_arrived_409(client):
    cid = _declare(client)["consignment_id"]
    assert client.post(f"/border/v1/consignments/{cid}/arrive",
                       headers=TARABA).status_code == 409


def test_invalid_transition_cleared_is_terminal(client):
    cid = _declare(client)["consignment_id"]
    client.post(f"/border/v1/consignments/{cid}/seal", headers=TARABA)
    _scan(client, cid)
    client.post(f"/border/v1/consignments/{cid}/arrive", headers=TARABA)
    client.post(f"/border/v1/consignments/{cid}/clear",
                json={"officer_ref": "off-7"}, headers=TARABA)
    assert client.post(f"/border/v1/consignments/{cid}/seal",
                       headers=TARABA).status_code == 409


def test_clear_before_arrive_409(client):
    cid = _declare(client)["consignment_id"]
    resp = client.post(f"/border/v1/consignments/{cid}/clear",
                       json={"officer_ref": "off-7"}, headers=TARABA)
    assert resp.status_code == 409


def test_unknown_consignment_404(client):
    assert client.get("/border/v1/consignments/nope",
                      headers=TARABA).status_code == 404


# --- levy math ---------------------------------------------------------------------


def test_levy_quote_math_kobo(client):
    # taraba: flat 250_000 kobo + 50 bps of 5_000_000_00 = 2_500_000
    quote = client.post("/border/v1/levy/quote",
                        json={"declared_value_kobo": 5_000_000_00},
                        headers=TARABA).json()
    assert quote["flat_fee_kobo"] == 250_000
    assert quote["ad_valorem_kobo"] == (5_000_000_00 * 50) // 10_000
    assert quote["total_kobo"] == 250_000 + 2_500_000


def test_levy_policy_differs_by_state(client):
    ogun = client.post("/border/v1/levy/quote",
                       json={"declared_value_kobo": 1_000_000},
                       headers={"X-State-Tenant": "ogun"}).json()
    sokoto = client.post("/border/v1/levy/quote",
                         json={"declared_value_kobo": 1_000_000},
                         headers={"X-State-Tenant": "sokoto"}).json()
    assert ogun["total_kobo"] == 350_000 + 7_500
    assert sokoto["total_kobo"] == 200_000 + 4_000


def test_levy_policy_update_and_default_floor(client):
    client.put("/border/v1/levy/policy",
               json={"flat_fee_kobo": 100, "ad_valorem_bps": 1},
               headers=TARABA)
    quote = client.post("/border/v1/levy/quote",
                        json={"declared_value_kobo": 999},
                        headers=TARABA).json()
    assert quote["ad_valorem_kobo"] == (999 * 1) // 10_000  # floors to 0
    assert quote["total_kobo"] == 100


def test_levy_assessment_double_entry_and_event(client, bus):
    con = _declare(client)
    resp = client.post(f"/border/v1/levy/assess/{con['consignment_id']}",
                       headers=TARABA)
    assert resp.status_code == 201
    body = resp.json()
    entry = body["ledger_entries"][0]
    assert entry["debit_account"] == "trader:trader-001"
    assert entry["credit_account"] == "state:taraba:transit_levy_revenue"
    assert entry["amount_kobo"] == body["total_kobo"]
    topics = [e["topic"] for e in bus.published]
    assert "ng.sos.border.levy_assessed" in topics
    assert "ng.sos.border.transit_crossing_recorded" in topics


# --- tamper detection -----------------------------------------------------------------


def test_seal_broken_raises_tamper_alert(client, bus):
    cid = _declare(client)["consignment_id"]
    client.post(f"/border/v1/consignments/{cid}/seal", headers=TARABA)
    _scan(client, cid, seal_intact=False)
    alerts = client.get("/border/v1/tamper-alerts", headers=TARABA).json()
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "seal_broken"
    assert any(e["topic"] == "ng.sos.border.tamper_alert"
               for e in bus.published)


def test_route_deviation_off_corridor(client):
    cid = _declare(client)["consignment_id"]
    client.post(f"/border/v1/consignments/{cid}/seal", headers=TARABA)
    _scan(client, cid, checkpoint="illela-niger")  # sokoto crossing, off-corridor
    alerts = client.get("/border/v1/tamper-alerts", headers=TARABA).json()
    assert any(a["kind"] == "route_deviation" for a in alerts)


def test_ordered_corridor_no_alert(client):
    cid = _declare(client)["consignment_id"]
    client.post(f"/border/v1/consignments/{cid}/seal", headers=TARABA)
    _scan(client, cid, checkpoint="gembu-cameroon")
    _scan(client, cid, checkpoint="ibi", direction="exit")
    assert client.get("/border/v1/tamper-alerts", headers=TARABA).json() == []


def test_route_deviation_out_of_order(client):
    cid = _declare(client)["consignment_id"]
    client.post(f"/border/v1/consignments/{cid}/seal", headers=TARABA)
    _scan(client, cid, checkpoint="ibi")
    _scan(client, cid, checkpoint="gembu-cameroon")  # regression
    alerts = client.get("/border/v1/tamper-alerts", headers=TARABA).json()
    assert any(a["kind"] == "route_deviation" for a in alerts)


# --- telematics / geofence ------------------------------------------------------------


def test_point_in_polygon_ray_cast():
    poly = [[9.0, 6.0], [12.0, 6.0], [12.0, 8.5], [9.0, 8.5]]
    assert point_in_polygon(7.0, 10.0, poly) is True
    assert point_in_polygon(9.0, 10.0, poly) is False


def test_ping_inside_corridor_no_alert(client):
    resp = client.post("/border/v1/telematics/pings", json={
        "truck_id": "TRK-1", "latitude": 7.0, "longitude": 10.0,
        "speed_kph": 60.0, "pinged_at": "2025-01-01T10:00:00+00:00",
    }, headers=TARABA)
    assert resp.status_code == 201
    assert resp.json()["in_corridor"] is True
    assert client.get("/border/v1/tamper-alerts", headers=TARABA).json() == []


def test_ping_outside_corridor_raises_geofence_alert(client):
    resp = client.post("/border/v1/telematics/pings", json={
        "truck_id": "TRK-2", "latitude": 9.5, "longitude": 10.0,
        "speed_kph": 80.0, "pinged_at": "2025-01-01T10:00:00+00:00",
        "consignment_id": "con-x",
    }, headers=TARABA)
    assert resp.json()["in_corridor"] is False
    alerts = client.get("/border/v1/tamper-alerts", headers=TARABA).json()
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "geofence_deviation"
    assert alerts[0]["consignment_id"] == "con-x"


# --- audit hash chain ----------------------------------------------------------------


def _cleared(client, headers, officer):
    con = _declare(client, headers=headers)
    cid = con["consignment_id"]
    client.post(f"/border/v1/consignments/{cid}/seal", headers=headers)
    _scan(client, cid, headers=headers)
    client.post(f"/border/v1/consignments/{cid}/arrive", headers=headers)
    client.post(f"/border/v1/consignments/{cid}/clear",
                json={"officer_ref": officer}, headers=headers)
    return cid


def test_clearance_chain_links(client):
    _cleared(client, TARABA, "off-1")
    _cleared(client, TARABA, "off-2")
    feed = client.get("/border/v1/clearance-audit", headers=TARABA).json()
    records = feed["records"]
    assert len(records) == 2
    assert records[0]["prev_hash"] == GENESIS_PREV_HASH
    assert records[1]["prev_hash"] == records[0]["event_hash"]
    assert feed["chain_errors"] == []


def test_clearance_chain_detects_tamper(store):
    from _shared.hashchain import event_payload_hash  # noqa: F401  (import check)
    store.declare_consignment("taraba", "t", "tag", "g", "120740", 100,
                              "gembu-cameroon", "ibi")
    con = store.list_consignments("taraba")[0]
    store.transition("taraba", con.consignment_id, "sealed")
    store.record_scan("taraba", con.consignment_id, "gembu-cameroon", "entry",
                      "2025-01-01T09:00:00+00:00", 6.7, 11.2)
    store.transition("taraba", con.consignment_id, "arrived")
    store.clear_consignment("taraba", con.consignment_id, "off-1")
    # Tamper with the recorded decision.
    rec = store.clearance_chain[0]
    store.clearance_chain[0] = rec.model_copy(update={"officer_ref": "forged"})
    errors = store.verify_clearance_chain()
    assert errors, "tampered record must fail verification"


# --- adapters ------------------------------------------------------------------------


def test_fixture_adapters_deterministic():
    rfid = FixtureRfidReaderAdapter()
    assert rfid.fetch_scans("taraba") == rfid.fetch_scans("taraba")
    tel = FixtureTelematicsAdapter()
    assert tel.fetch_pings("borno") == tel.fetch_pings("borno")


def test_production_profile_fail_closed_without_url():
    with pytest.raises(AdapterUnavailableError):
        build_rfid_adapter({"SOS_BORDER_PROFILE": "production"})
    with pytest.raises(AdapterUnavailableError):
        build_telematics_adapter({"SOS_BORDER_PROFILE": "production"})


def test_production_profile_with_url_builds():
    rfid = build_rfid_adapter({"SOS_BORDER_PROFILE": "production",
                               "SOS_BORDER_RFID_URL": "http://rfid:9000"})
    assert rfid is not None
    tel = build_telematics_adapter({"SOS_BORDER_PROFILE": "production",
                                    "SOS_BORDER_TELEMATICS_URL": "http://t:9000"})
    assert tel is not None


def test_unknown_profile_rejected():
    with pytest.raises(AdapterUnavailableError):
        build_rfid_adapter({"SOS_BORDER_PROFILE": "bogus"})


def test_live_adapter_call_failure_is_fail_closed():
    rfid = build_rfid_adapter({"SOS_BORDER_PROFILE": "production",
                               "SOS_BORDER_RFID_URL": "http://127.0.0.1:1"})
    with pytest.raises(AdapterUnavailableError):
        rfid.fetch_scans("taraba")


# --- health / fixture determinism via API ---------------------------------------------


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_fixture_pull_endpoints(client):
    scans = client.post("/border/v1/scans/pull", headers=TARABA).json()["scans"]
    assert scans == client.post("/border/v1/scans/pull", headers=TARABA).json()["scans"]
    pings = client.post("/border/v1/telematics/pull", headers=BORNO).json()["pings"]
    assert pings == client.post("/border/v1/telematics/pull",
                                headers=BORNO).json()["pings"]
