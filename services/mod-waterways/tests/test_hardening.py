"""Hardening tests: ticket idempotency, survey dedupe, integer royalty math."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import (
    FARE_POLICIES,
    SurveyDuplicateError,
    TicketIdempotencyConflict,
    WaterwaysStore,
    royalty_kobo_for,
    volume_to_mm3,
)
from app.main import create_app

LAGOS = {"X-State-Tenant": "lagos"}

LAGOON_POLYGON = [
    [3.4000, 6.4500],
    [3.4009, 6.4500],
    [3.4009, 6.4509],
    [3.4000, 6.4509],
    [3.4000, 6.4500],
]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _trip(client: TestClient, capacity: int = 50) -> str:
    resp = client.post("/waterways/v1/trips", headers=LAGOS, json={
        "route_id": "rt-lagos-01", "vessel": "MV Fixture",
        "capacity": capacity, "departure": "2026-06-01T08:00:00Z",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["trip_id"]


def _dredger(client: TestClient) -> str:
    resp = client.post("/waterways/v1/dredgers", headers=LAGOS, json={
        "vessel_name": "Dredger One", "license_no": "NIWA-001",
        "operator_kyb_ref": "kyb-1", "monthly_quota_m3": 10000,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["dredger_id"]


def _survey(client: TestClient, dredger_id: str, volume: float = 100.0,
            surveyed_at: str = "2026-03-10T12:00:00Z",
            dedupe_key: str | None = None):
    body = {"dredger_id": dredger_id, "polygon": LAGOON_POLYGON,
            "volume_m3": volume, "surveyed_at": surveyed_at}
    if dedupe_key:
        body["dedupe_key"] = dedupe_key
    return client.post("/waterways/v1/surveys", headers=LAGOS, json=body)


# --- B4: ticket idempotency ----------------------------------------------------

def test_ticket_idempotency_replay_returns_original(client: TestClient) -> None:
    trip = _trip(client)
    body = {"trip_id": trip, "passenger_name": "Ada Lovelace"}
    first = client.post("/waterways/v1/tickets", headers={**LAGOS, "Idempotency-Key": "k1"},
                        json=body)
    assert first.status_code == 201
    replay = client.post("/waterways/v1/tickets", headers={**LAGOS, "Idempotency-Key": "k1"},
                         json=body)
    assert replay.status_code == 201
    assert replay.json() == first.json()  # identical ticket, no double issue
    tickets = client.get("/waterways/v1/tickets", headers=LAGOS).json()
    assert len(tickets) == 1


def test_ticket_idempotency_conflict_different_payload(client: TestClient) -> None:
    trip = _trip(client)
    client.post("/waterways/v1/tickets", headers={**LAGOS, "Idempotency-Key": "k2"},
                json={"trip_id": trip, "passenger_name": "Ada"})
    conflict = client.post("/waterways/v1/tickets",
                           headers={**LAGOS, "Idempotency-Key": "k2"},
                           json={"trip_id": trip, "passenger_name": "Grace"})
    assert conflict.status_code == 409
    tickets = client.get("/waterways/v1/tickets", headers=LAGOS).json()
    assert len(tickets) == 1


def test_ticket_idempotency_key_scoped_per_tenant(client: TestClient) -> None:
    trip = _trip(client)
    client.post("/waterways/v1/tickets", headers={**LAGOS, "Idempotency-Key": "k3"},
                json={"trip_id": trip, "passenger_name": "Ada"})
    # Same key under another tenant is an independent namespace (409 from
    # payload mismatch must NOT leak across tenants; here a different trip
    # tenant lookup fails first with 403 since trips are tenant-scoped).
    other = client.post("/waterways/v1/tickets",
                        headers={"X-State-Tenant": "bayelsa", "Idempotency-Key": "k3"},
                        json={"trip_id": trip, "passenger_name": "Ada"})
    assert other.status_code == 403


def test_ticket_without_idempotency_key_unchanged(client: TestClient) -> None:
    trip = _trip(client)
    a = client.post("/waterways/v1/tickets", headers=LAGOS,
                    json={"trip_id": trip, "passenger_name": "Ada"})
    b = client.post("/waterways/v1/tickets", headers=LAGOS,
                    json={"trip_id": trip, "passenger_name": "Ada"})
    assert a.status_code == b.status_code == 201
    assert a.json()["ticket_id"] != b.json()["ticket_id"]


def test_ticket_idempotency_store_level_conflict() -> None:
    store = WaterwaysStore()
    trip = store.schedule_trip("lagos", "rt-lagos-01", "MV X", 10, "2026-06-01T08:00:00Z")
    t1 = store.purchase_ticket("lagos", trip.trip_id, "Ada", idempotency_key="kk")
    assert store.purchase_ticket("lagos", trip.trip_id, "Ada",
                                 idempotency_key="kk").ticket_id == t1.ticket_id
    with pytest.raises(TicketIdempotencyConflict):
        store.purchase_ticket("lagos", trip.trip_id, "Grace", idempotency_key="kk")


# --- B5: survey dedupe -----------------------------------------------------------

def test_survey_duplicate_natural_key_409(client: TestClient) -> None:
    dredger = _dredger(client)
    first = _survey(client, dredger)
    assert first.status_code == 201, first.text
    dup = _survey(client, dredger)  # identical payload → natural dedupe key
    assert dup.status_code == 409
    surveys = client.get("/waterways/v1/surveys", headers=LAGOS).json()
    assert len(surveys) == 1
    royalties = client.get("/waterways/v1/royalties", headers=LAGOS).json()
    assert len(royalties["assessments"]) == 1  # no double assessment
    assert royalties["chain_errors"] == []


def test_survey_explicit_dedupe_key_409(client: TestClient) -> None:
    dredger = _dredger(client)
    assert _survey(client, dredger, dedupe_key="surv-2026-03-01").status_code == 201
    dup = _survey(client, dredger, volume=55.5, surveyed_at="2026-03-11T09:00:00Z",
                  dedupe_key="surv-2026-03-01")
    assert dup.status_code == 409


def test_survey_different_payload_new_assessment(client: TestClient) -> None:
    dredger = _dredger(client)
    assert _survey(client, dredger, volume=100.0).status_code == 201
    ok = _survey(client, dredger, volume=50.0,
                 surveyed_at="2026-03-12T12:00:00Z")
    assert ok.status_code == 201
    royalties = client.get("/waterways/v1/royalties", headers=LAGOS).json()
    assert len(royalties["assessments"]) == 2


def test_survey_dedupe_store_level() -> None:
    store = WaterwaysStore()
    d = store.register_dredger("lagos", "V", "L1", "kyb-1", 10000)
    store.ingest_survey("lagos", d.dredger_id, LAGOON_POLYGON, 10.0,
                        "2026-03-10T12:00:00Z", dedupe_key="k")
    with pytest.raises(SurveyDuplicateError):
        store.ingest_survey("lagos", d.dredger_id, LAGOON_POLYGON, 99.0,
                            "2026-03-11T12:00:00Z", dedupe_key="k")
    assert len(store.royalty_assessments) == 1


# --- B6: integer royalty math ------------------------------------------------------

def test_royalty_integer_math_exactness() -> None:
    # 120_000 kobo/m³ (lagos); fractional m³ must not round via float.
    assert royalty_kobo_for(2.5, 120_000) == 300_000
    assert royalty_kobo_for(0.001, 120_000) == 120
    assert royalty_kobo_for(1.2345, 120_000) == 1234 * 120_000 // 1000  # floor
    assert royalty_kobo_for(0.0009, 120_000) == 0  # sub-mm³ floors to zero
    assert isinstance(royalty_kobo_for(2.5, 120_000), int)


def test_volume_to_mm3_decimal_exact() -> None:
    assert volume_to_mm3(0.1) == 100  # float 0.1 is inexact; Decimal is not
    assert volume_to_mm3(1.234) == 1234


def test_royalty_assessment_integer_kobo_end_to_end(client: TestClient) -> None:
    dredger = _dredger(client)
    resp = _survey(client, dredger, volume=2.5)
    assert resp.status_code == 201
    assessment = resp.json()["royalty_assessment"]
    rate = FARE_POLICIES["lagos"].royalty_kobo_per_m3
    assert assessment["royalty_kobo"] == 2500 * rate // 1000
    assert isinstance(assessment["royalty_kobo"], int)
