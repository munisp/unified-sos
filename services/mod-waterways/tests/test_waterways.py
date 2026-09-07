"""Tests for mod-waterways — ferry e-ticketing & dredging volumetrics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters import (
    AdapterUnavailableError,
    FixtureAisAdapter,
    FixtureSedonaAdapter,
    HttpAisAdapter,
    HttpSedonaAdapter,
    build_ais_adapter,
    build_sedona_adapter,
)
from app.domain import (
    FARE_POLICIES,
    TOPIC_ROYALTY_ASSESSED,
    TOPIC_TICKET_SOLD,
    TOPIC_VOLUME_ALERT,
    WaterwaysStore,
)
from app.main import create_app

LAGOS = {"X-State-Tenant": "lagos"}
BAYELSA = {"X-State-Tenant": "bayelsa"}

# ~100 m × 100 m square in the Lagos lagoon (inside the lagos geofence bbox).
LAGOON_POLYGON = [
    [3.4000, 6.4500],
    [3.4009, 6.4500],
    [3.4009, 6.4509],
    [3.4000, 6.4509],
    [3.4000, 6.4500],
]

# Yenagoa-area polygon (inside the bayelsa geofence bbox).
BAYELSA_POLYGON = [
    [6.2600, 4.9200],
    [6.2609, 4.9200],
    [6.2609, 4.9209],
    [6.2600, 4.9209],
]


@pytest.fixture()
def store() -> WaterwaysStore:
    return WaterwaysStore()


@pytest.fixture()
def client(store: WaterwaysStore) -> TestClient:
    return TestClient(create_app(store=store))


def _make_trip(client: TestClient, capacity: int = 2, headers=None) -> dict:
    resp = client.post("/waterways/v1/trips", headers=headers or LAGOS, json={
        "route_id": "rt-lagos-01", "vessel": "MV Adéyẹmí",
        "capacity": capacity, "departure": "2025-06-01T08:00:00+00:00",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_dredger(client: TestClient, quota: float = 1000.0, headers=None) -> dict:
    resp = client.post("/waterways/v1/dredgers", headers=headers or LAGOS, json={
        "vessel_name": "Dredger Niger Queen", "license_no": "NIWA-LAG-0042",
        "operator_kyb_ref": "kyb-abc123", "monthly_quota_m3": quota,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- tenant scoping ------------------------------------------------------------

def test_healthz_no_tenant_needed(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_metrics_exposed(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "service_info" in resp.text


def test_missing_tenant_header_is_400(client):
    assert client.get("/waterways/v1/routes").status_code == 400


def test_unknown_tenant_is_400(client):
    resp = client.get("/waterways/v1/routes", headers={"X-State-Tenant": "imo"})
    assert resp.status_code == 400


# --- route / jetty registry ------------------------------------------------------

def test_route_fixtures_all_adoption_states(client):
    for state, expected in {
        "lagos": 2, "bayelsa": 1, "rivers": 1, "benue": 1,
        "delta": 1, "kogi": 1, "niger": 1,
    }.items():
        resp = client.get("/waterways/v1/routes", headers={"X-State-Tenant": state})
        assert resp.status_code == 200
        assert len(resp.json()) == expected, state
    names = [r["name"] for r in client.get("/waterways/v1/routes", headers=LAGOS).json()]
    assert names == ["Ikorodu–CMS", "Badagry–Marina"]


def test_jetties_listed(client):
    resp = client.get("/waterways/v1/jetties", headers=BAYELSA)
    assert resp.json()["jetties"] == ["Yenagoa Jetty", "Brass Terminal"]


# --- ticketing ----------------------------------------------------------------------

def test_trip_schedule_and_list(client):
    trip = _make_trip(client, capacity=50)
    trips = client.get("/waterways/v1/trips", headers=LAGOS).json()
    assert [t["trip_id"] for t in trips] == [trip["trip_id"]]
    # tenant isolation: not visible from bayelsa
    assert client.get("/waterways/v1/trips", headers=BAYELSA).json() == []


def test_trip_schedule_unknown_route_404(client):
    resp = client.post("/waterways/v1/trips", headers=LAGOS, json={
        "route_id": "rt-lagos-99", "vessel": "MV X", "capacity": 10,
        "departure": "2025-06-01T08:00:00+00:00",
    })
    assert resp.status_code == 404


def test_fare_math_integer_kobo(client):
    # lagos: base 50_000 + 1_500/km × 24 km (Ikorodu–CMS) = 86_000 kobo
    trip = _make_trip(client, capacity=5)
    resp = client.post("/waterways/v1/tickets", headers=LAGOS, json={
        "trip_id": trip["trip_id"], "passenger_name": "Adaeze Okafor",
    })
    assert resp.status_code == 201, resp.text
    ticket = resp.json()
    policy = FARE_POLICIES["lagos"]
    assert ticket["fare_kobo"] == policy.base_fare_kobo + policy.per_km_kobo * 24
    assert isinstance(ticket["fare_kobo"], int)
    assert ticket["qr_ref"].startswith("WWY-LAG-")


def test_capacity_enforcement_409_sold_out(client):
    trip = _make_trip(client, capacity=2)
    for name in ("A", "B"):
        resp = client.post("/waterways/v1/tickets", headers=LAGOS,
                           json={"trip_id": trip["trip_id"], "passenger_name": name})
        assert resp.status_code == 201
    resp = client.post("/waterways/v1/tickets", headers=LAGOS,
                       json={"trip_id": trip["trip_id"], "passenger_name": "C"})
    assert resp.status_code == 409


def test_ticket_sold_event_published(client):
    store = client.app.state.store
    trip = _make_trip(client, capacity=3)
    client.post("/waterways/v1/tickets", headers=LAGOS,
                json={"trip_id": trip["trip_id"], "passenger_name": "A"})
    topics = [e["topic"] for e in client.app.state.event_bus.published]
    assert TOPIC_TICKET_SOLD in topics


def test_manifest_generation(client):
    trip = _make_trip(client, capacity=3)
    client.post("/waterways/v1/tickets", headers=LAGOS,
                json={"trip_id": trip["trip_id"], "passenger_name": "A"})
    manifest = client.get(f"/waterways/v1/trips/{trip['trip_id']}/manifest",
                          headers=LAGOS).json()
    assert manifest["passenger_count"] == 1
    assert manifest["manifest_locked"] is False
    assert manifest["passengers"][0]["passenger_name"] == "A"


def test_manifest_locked_at_departure(client):
    trip = _make_trip(client, capacity=3)
    client.post("/waterways/v1/tickets", headers=LAGOS,
                json={"trip_id": trip["trip_id"], "passenger_name": "A"})
    dep = client.post(f"/waterways/v1/trips/{trip['trip_id']}/depart", headers=LAGOS)
    assert dep.status_code == 200
    assert dep.json()["manifest_locked"] is True
    # no further sales after departure
    resp = client.post("/waterways/v1/tickets", headers=LAGOS,
                       json={"trip_id": trip["trip_id"], "passenger_name": "B"})
    assert resp.status_code == 409
    # manifest still readable, marked locked
    manifest = client.get(f"/waterways/v1/trips/{trip['trip_id']}/manifest",
                          headers=LAGOS).json()
    assert manifest["manifest_locked"] is True
    assert manifest["passenger_count"] == 1
    # double departure is a conflict
    assert client.post(f"/waterways/v1/trips/{trip['trip_id']}/depart",
                       headers=LAGOS).status_code == 409


def test_cross_tenant_trip_access_403(client):
    trip = _make_trip(client, capacity=3)
    resp = client.post("/waterways/v1/tickets", headers=BAYELSA,
                       json={"trip_id": trip["trip_id"], "passenger_name": "A"})
    assert resp.status_code == 403
    assert client.get(f"/waterways/v1/trips/{trip['trip_id']}/manifest",
                      headers=BAYELSA).status_code == 403


# --- dredging volumetrics ---------------------------------------------------------------

def test_dredger_register_and_list(client):
    dredger = _make_dredger(client)
    assert dredger["operator_kyb_ref"] == "kyb-abc123"
    listed = client.get("/waterways/v1/dredgers", headers=LAGOS).json()
    assert [d["dredger_id"] for d in listed] == [dredger["dredger_id"]]
    assert client.get("/waterways/v1/dredgers", headers=BAYELSA).json() == []


def test_survey_ingest_and_royalty_math(client):
    dredger = _make_dredger(client, quota=10_000.0)
    resp = client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"], "polygon": LAGOON_POLYGON,
        "volume_m3": 500.0, "surveyed_at": "2025-06-10T12:00:00+00:00",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # lagos policy: 120_000 kobo per m³ × 500 m³ = 60_000_000 kobo
    assert body["royalty_assessment"]["royalty_kobo"] == 500.0 * 120_000
    assert body["royalty_assessment"]["over_quota"] is False
    assert body["survey"]["verified_volume_m3"] is not None


def test_over_quota_alert_event(client):
    dredger = _make_dredger(client, quota=800.0)
    for ts, vol in (("2025-06-01T10:00:00+00:00", 500.0),
                    ("2025-06-02T10:00:00+00:00", 400.0)):
        resp = client.post("/waterways/v1/surveys", headers=LAGOS, json={
            "dredger_id": dredger["dredger_id"], "polygon": LAGOON_POLYGON,
            "volume_m3": vol, "surveyed_at": ts,
        })
        assert resp.status_code == 201
    assert resp.json()["over_quota"] is True
    topics = [e["topic"] for e in client.app.state.event_bus.published]
    assert TOPIC_VOLUME_ALERT in topics
    assert topics.count(TOPIC_ROYALTY_ASSESSED) == 2


def test_quota_status_monthly_rollup(client):
    dredger = _make_dredger(client, quota=800.0)
    client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"], "polygon": LAGOON_POLYGON,
        "volume_m3": 500.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    })
    status_rows = client.get("/waterways/v1/quotas", headers=LAGOS).json()
    row = next(r for r in status_rows if r["month"] == "2025-06")
    assert row["monthly_cumulative_m3"] == 500.0
    assert row["over_quota"] is False
    assert row["utilization"] == 0.625


def test_geofence_polygon_validation(client):
    dredger = _make_dredger(client)
    # too few vertices
    assert client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"], "polygon": [[3.4, 6.4], [3.5, 6.4]],
        "volume_m3": 10.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    }).status_code == 422
    # outside Nigeria bounds
    assert client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"],
        "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        "volume_m3": 10.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    }).status_code == 422
    # valid polygon but centroid outside the lagos state geofence (bayelsa area)
    assert client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"], "polygon": BAYELSA_POLYGON,
        "volume_m3": 10.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    }).status_code == 422


def test_survey_tenant_isolation(client):
    dredger = _make_dredger(client)
    client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"], "polygon": LAGOON_POLYGON,
        "volume_m3": 100.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    })
    assert client.get("/waterways/v1/surveys", headers=BAYELSA).json() == []
    # cross-tenant survey against a lagos dredger from bayelsa → 403
    resp = client.post("/waterways/v1/surveys", headers=BAYELSA, json={
        "dredger_id": dredger["dredger_id"], "polygon": BAYELSA_POLYGON,
        "volume_m3": 100.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    })
    assert resp.status_code == 403


def test_royalties_audit_feed_and_chain_integrity(client):
    dredger = _make_dredger(client)
    for i in range(3):
        client.post("/waterways/v1/surveys", headers=LAGOS, json={
            "dredger_id": dredger["dredger_id"], "polygon": LAGOON_POLYGON,
            "volume_m3": 100.0, "surveyed_at": f"2025-06-0{i + 1}T10:00:00+00:00",
        })
    feed = client.get("/waterways/v1/royalties", headers=LAGOS).json()
    assert feed["chain_errors"] == []
    assert len(feed["assessments"]) == 3
    # genesis linkage
    assert feed["assessments"][0]["prev_hash"] == "0" * 64


def test_hash_chain_tamper_detection(client):
    store = client.app.state.store
    dredger = _make_dredger(client)
    client.post("/waterways/v1/surveys", headers=LAGOS, json={
        "dredger_id": dredger["dredger_id"], "polygon": LAGOON_POLYGON,
        "volume_m3": 100.0, "surveyed_at": "2025-06-01T10:00:00+00:00",
    })
    assert store.verify_royalty_chain("lagos") == []
    store.royalty_assessments[0]["royalty_kobo"] = 1  # tamper
    errors = store.verify_royalty_chain("lagos")
    assert errors and "tampered" in errors[0]


# --- adapters -----------------------------------------------------------------------------

def test_sedona_fixture_determinism():
    adapter = FixtureSedonaAdapter()
    v1 = adapter.verify_volume(LAGOON_POLYGON, 3.0)
    v2 = adapter.verify_volume(LAGOON_POLYGON, 3.0)
    assert v1 == v2
    assert v1.polygon_area_m2 > 0
    assert v1.verified_volume_m3 == pytest.approx(v1.polygon_area_m2 * 3.0)


def test_sedona_fixture_rejects_bad_input():
    adapter = FixtureSedonaAdapter()
    with pytest.raises(ValueError):
        adapter.verify_volume([[3.4, 6.4]], 3.0)
    with pytest.raises(ValueError):
        adapter.verify_volume(LAGOON_POLYGON, 0.0)


def test_ais_fixture_determinism():
    adapter = FixtureAisAdapter()
    p1 = adapter.latest_position("657123400")
    p2 = adapter.latest_position("657123400")
    assert p1 == p2
    assert 4.5 <= p1.latitude <= 11.5
    with pytest.raises(ValueError):
        adapter.latest_position("not-a-mmsi")


def test_adapters_fail_closed_in_production_without_config():
    with pytest.raises(AdapterUnavailableError):
        build_sedona_adapter({"SOS_WATERWAYS_PROFILE": "production"})
    with pytest.raises(AdapterUnavailableError):
        build_ais_adapter({"SOS_WATERWAYS_PROFILE": "production"})


def test_adapters_production_with_config():
    sedona = build_sedona_adapter({"SOS_WATERWAYS_PROFILE": "production",
                                   "SOS_WATERWAYS_SEDONA_URL": "http://sedona:9000"})
    assert isinstance(sedona, HttpSedonaAdapter)
    ais = build_ais_adapter({"SOS_WATERWAYS_PROFILE": "live",
                             "SOS_WATERWAYS_AIS_URL": "http://ais:9001"})
    assert isinstance(ais, HttpAisAdapter)


def test_adapters_unknown_profile_fail_closed():
    with pytest.raises(AdapterUnavailableError):
        build_sedona_adapter({"SOS_WATERWAYS_PROFILE": "staging"})
    with pytest.raises(AdapterUnavailableError):
        build_ais_adapter({"SOS_WATERWAYS_PROFILE": "staging"})


def test_adapters_fixture_profile_default():
    assert isinstance(build_sedona_adapter({}), FixtureSedonaAdapter)
    assert isinstance(build_ais_adapter({"SOS_WATERWAYS_PROFILE": "test"}), FixtureAisAdapter)


def test_dredger_position_endpoint_uses_ais(client):
    dredger = _make_dredger(client)
    resp = client.get(f"/waterways/v1/dredgers/{dredger['dredger_id']}/position",
                      headers=LAGOS, params={"mmsi": "657123400"})
    assert resp.status_code == 200
    assert resp.json()["mmsi"] == "657123400"
    assert client.get("/waterways/v1/dredgers/drg-nope/position",
                      headers=LAGOS, params={"mmsi": "657123400"}).status_code == 404
