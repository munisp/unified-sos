"""API tests for the cadastre-parcels.yaml contract: happy path, overlap 409,
tenancy isolation, spatial/attribute search."""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.main import create_app
from tests.helpers import BASE_LAT, BASE_LON, registration_body, square_geojson


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


OGUN = "/api/v1/states/ogun/cadastre/parcels"
LAGOS = "/api/v1/states/lagos/cadastre/parcels"


class TestRegisterParcel:
    def test_happy_path_201_and_workflow_initiated(self, client):
        body = registration_body("OG-ABK-0001", square_geojson(BASE_LON, BASE_LAT))
        resp = client.post(OGUN, json=body)
        assert resp.status_code == 201, resp.text
        parcel = resp.json()
        assert parcel["parcel_uin"] == "OG-ABK-0001"
        assert parcel["title_type"] == "UNREGISTERED"
        assert parcel["status"] == "ACTIVE"
        assert parcel["titling_workflow_id"].startswith("cadastral-titling-")

    def test_overlap_rejected_409(self, client):
        client.post(OGUN, json=registration_body("OG-ABK-0002", square_geojson(BASE_LON, BASE_LAT)))
        overlapping = registration_body(
            "OG-ABK-0003", square_geojson(BASE_LON + 0.0005, BASE_LAT + 0.0005)
        )
        resp = client.post(OGUN, json=overlapping)
        assert resp.status_code == 409
        assert "overlap" in resp.json()["detail"].lower()

    def test_touching_neighbour_accepted(self, client):
        client.post(OGUN, json=registration_body("OG-ABK-0004", square_geojson(BASE_LON, BASE_LAT)))
        touching = registration_body("OG-ABK-0005", square_geojson(BASE_LON + 0.001, BASE_LAT))
        assert client.post(OGUN, json=touching).status_code == 201

    def test_duplicate_uin_rejected_409(self, client):
        body = registration_body("OG-ABK-0006", square_geojson(BASE_LON, BASE_LAT))
        assert client.post(OGUN, json=body).status_code == 201
        moved = registration_body("OG-ABK-0006", square_geojson(BASE_LON + 0.01, BASE_LAT))
        assert client.post(OGUN, json=moved).status_code == 409

    def test_invalid_geometry_422(self, client):
        body = registration_body(
            "OG-ABK-0007", {"type": "Point", "coordinates": [BASE_LON, BASE_LAT]}
        )
        assert client.post(OGUN, json=body).status_code == 422

    def test_declared_area_mismatch_422(self, client):
        geojson = square_geojson(BASE_LON, BASE_LAT)
        body = registration_body("OG-ABK-0008", geojson, area_sqm=1.0)
        assert client.post(OGUN, json=body).status_code == 422

    def test_beacon_count_floor_enforced(self, client):
        body = registration_body(
            "OG-ABK-0009", square_geojson(BASE_LON + 0.02, BASE_LAT), beacon_count=2
        )
        assert client.post(OGUN, json=body).status_code == 422


class TestTenancyIsolation:
    def test_same_boundary_allowed_in_different_state(self, client):
        geojson = square_geojson(BASE_LON, BASE_LAT)
        assert client.post(OGUN, json=registration_body("OG-X-1", geojson)).status_code == 201
        # Identical geometry under a different tenant must NOT conflict (RLS scoping).
        assert client.post(LAGOS, json=registration_body("LA-X-1", geojson)).status_code == 201

    def test_search_does_not_leak_across_tenants(self, client):
        client.post(OGUN, json=registration_body("OG-Y-1", square_geojson(BASE_LON, BASE_LAT)))
        client.post(LAGOS, json=registration_body("LA-Y-1", square_geojson(BASE_LON + 0.01, BASE_LAT)))
        ogun_uins = {p["parcel_uin"] for p in client.get(OGUN).json()}
        lagos_uins = {p["parcel_uin"] for p in client.get(LAGOS).json()}
        assert ogun_uins == {"OG-Y-1"}
        assert lagos_uins == {"LA-Y-1"}

    def test_unknown_state_rejected(self, client):
        resp = client.get("/api/v1/states/kano/cadastre/parcels")
        assert resp.status_code == 422  # path enum per contract StateId


class TestSearch:
    def test_lga_filter(self, client):
        client.post(OGUN, json=registration_body("OG-Z-1", square_geojson(BASE_LON, BASE_LAT)))
        client.post(
            OGUN,
            json=registration_body(
                "OG-Z-2", square_geojson(BASE_LON + 0.01, BASE_LAT), lga_id="ijebu-ode"
            ),
        )
        res = client.get(OGUN, params={"lga_id": "ijebu-ode"}).json()
        assert [p["parcel_uin"] for p in res] == ["OG-Z-2"]

    def test_within_wkt_spatial_filter(self, client):
        client.post(OGUN, json=registration_body("OG-W-1", square_geojson(BASE_LON, BASE_LAT)))
        client.post(OGUN, json=registration_body("OG-W-2", square_geojson(BASE_LON + 0.05, BASE_LAT)))
        # Window covering only the first parcel.
        wkt = (
            f"POLYGON(({BASE_LON - 0.0005} {BASE_LAT - 0.0005}, "
            f"{BASE_LON + 0.002} {BASE_LAT - 0.0005}, {BASE_LON + 0.002} {BASE_LAT + 0.002}, "
            f"{BASE_LON - 0.0005} {BASE_LAT + 0.002}, {BASE_LON - 0.0005} {BASE_LAT - 0.0005}))"
        )
        res = client.get(OGUN, params={"within": wkt}).json()
        assert [p["parcel_uin"] for p in res] == ["OG-W-1"]

    def test_invalid_within_wkt_422(self, client):
        assert client.get(OGUN, params={"within": "NOT WKT"}).status_code == 422
