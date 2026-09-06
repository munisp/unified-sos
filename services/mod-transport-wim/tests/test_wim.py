"""mod-transport-wim: overload detection, fines, ANPR correlation, manifests."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import WIMService

CORRIDOR = {
    "corridor_id": "COR-OGN-SAGAMU-EWK",
    "state_id": "ogun",
    "single_axle_limit_kg": 10_000.0,
    "tandem_axle_limit_kg": 18_000.0,
    "gvw_limit_kg": 48_000.0,
    "fine_base_kobo": 5_000_000,  # NGN 50,000
    "fine_per_overload_kg_kobo": 1_000,  # NGN 10 per kg over
    "tolerance_pct": 0.0,
}


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        assert c.post("/corridors", json=CORRIDOR).status_code == 201
        yield c


def reading(reading_id, axles, plate=None, recorded_at=None):
    return {
        "reading_id": reading_id,
        "corridor_id": CORRIDOR["corridor_id"],
        "station_id": "WIM-SAGAMU-KM12",
        "axle_weights_kg": axles,
        "speed_kmh": 82.0,
        "vehicle_plate": plate,
        **({"recorded_at": recorded_at} if recorded_at else {}),
    }


class TestOverloadDetection:
    def test_legal_load_passes(self, client):
        r = client.post("/wim/readings", json=reading("RD-1", [9_000, 8_500, 8_500, 9_000]))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["overload_detected"] is False
        assert body["gvw_kg"] == 35_000.0
        assert client.get("/fines").json() == []

    def test_single_axle_overload_fined(self, client):
        r = client.post("/wim/readings", json=reading("RD-2", [12_000, 8_000, 8_000]))
        body = r.json()
        assert body["overload_detected"] is True
        assert body["axle_violations"][0]["overload_kg"] == 2_000.0
        fines = client.get("/fines").json()
        assert len(fines) == 1
        assert fines[0]["amount_kobo"] == 5_000_000 + int(2_000 * 1_000)
        assert fines[0]["transfer_code"] == 120
        assert fines[0]["status"] == "ASSESSED"

    def test_gvw_overload_fined(self, client):
        r = client.post(
            "/wim/readings", json=reading("RD-3", [9_500, 9_500, 9_500, 9_500, 9_500, 9_500])
        )
        body = r.json()
        assert body["gvw_kg"] == 57_000.0
        assert body["gvw_overload_kg"] == 9_000.0
        fines = client.get("/fines").json()
        assert fines[0]["total_overload_kg"] == 9_000.0

    def test_tandem_group_assessed_against_tandem_limit(self, client):
        # both axles individually > single limit; combined 19,500 > tandem 18,000
        r = client.post("/wim/readings", json=reading("RD-4", [10_500, 9_000 + 1_500]))
        # 10,500 + 10,500 both over single limit... adjust to clear tandem case:
        # axles 10,500 & 10,500: each > single (10,000), combined 21,000 > 18,000
        body = r.json()
        assert body["overload_detected"] is True
        v = body["axle_violations"][0]
        assert v["limit_kg"] == 18_000.0  # tandem limit applied to the pair

    def test_tolerance_waives_marginal_overload(self, client):
        tol = dict(CORRIDOR, corridor_id="COR-TOL", tolerance_pct=5.0)
        client.post("/corridors", json=tol)
        r = client.post(
            "/wim/readings",
            json=reading("RD-5", [10_400, 8_000, 8_000]) | {"corridor_id": "COR-TOL"},
        )
        assert r.json()["overload_detected"] is False  # 10,400 < 10,500 limit

    def test_unknown_corridor_404(self, client):
        r = client.post(
            "/wim/readings", json=reading("RD-6", [1_000]) | {"corridor_id": "COR-GHOST"}
        )
        assert r.status_code == 404


class TestANPRCorrelation:
    def test_plate_attached_from_nearby_anpr_event(self, client):
        now = datetime.now(timezone.utc)
        client.post(
            "/anpr/events",
            json={
                "event_id": "ANPR-1",
                "corridor_id": CORRIDOR["corridor_id"],
                "camera_id": "CAM-SAG-04",
                "vehicle_plate": "OGN-552-KJ",
                "captured_at": (now - timedelta(seconds=8)).isoformat(),
            },
        )
        r = client.post(
            "/wim/readings",
            json=reading("RD-7", [12_000, 8_000, 8_000], recorded_at=now.isoformat()),
        )
        assert r.status_code == 201
        fines = client.get("/fines", params={"vehicle_plate": "OGN-552-KJ"}).json()
        assert len(fines) == 1
        assert fines[0]["vehicle_plate"] == "OGN-552-KJ"

    def test_stale_anpr_not_correlated(self, client):
        now = datetime.now(timezone.utc)
        client.post(
            "/anpr/events",
            json={
                "event_id": "ANPR-2",
                "corridor_id": CORRIDOR["corridor_id"],
                "camera_id": "CAM-SAG-04",
                "vehicle_plate": "OGN-999-XX",
                "captured_at": (now - timedelta(minutes=5)).isoformat(),
            },
        )
        client.post(
            "/wim/readings",
            json=reading("RD-8", [12_000, 8_000, 8_000], recorded_at=now.isoformat()),
        )
        fines = client.get("/fines").json()
        assert fines[0]["vehicle_plate"] is None  # outside correlation window


class TestEvents:
    def test_weighbridge_event_published_on_shared_envelope(self):
        published = []

        class Bus:
            def publish(self, topic, payload):
                published.append((topic, payload))

        app = create_app(WIMService(bus=Bus()))
        with TestClient(app) as client:
            client.post("/corridors", json=CORRIDOR)
            client.post("/wim/readings", json=reading("RD-9", [12_000, 8_000, 8_000]))
        assert len(published) == 1
        topic, payload = published[0]
        assert topic == "ng.sos.mining.weighbridge_reading"
        assert payload.overload_detected is True
        assert payload.gross_weight_kg == 28_000.0


class TestEManifest:
    MANIFEST = {
        "manifest_id": "MAN-OGN-2026-8841",
        "state_id": "ogun",
        "vehicle_plate": "OGN-552-KJ",
        "consignment_refs": ["MIN-OGN-2026-100"],
        "cargo_description": "Granite chippings",
        "origin": "EWEKORO",
        "destination": "LAGOS_APAPA",
    }

    def test_register_and_verify(self, client):
        assert client.post("/manifests", json=self.MANIFEST).status_code == 201
        r = client.get(f"/manifests/{self.MANIFEST['manifest_id']}/verify")
        body = r.json()
        assert body["found"] and body["valid"]
        assert body["vehicle_plate"] == "OGN-552-KJ"

    def test_unknown_manifest_invalid(self, client):
        body = client.get("/manifests/MAN-GHOST/verify").json()
        assert body["found"] is False and body["valid"] is False

    def test_revoked_manifest_invalid(self, client):
        revoked = dict(self.MANIFEST, manifest_id="MAN-REVK", valid=False)
        client.post("/manifests", json=revoked)
        body = client.get("/manifests/MAN-REVK/verify").json()
        assert body["found"] is True and body["valid"] is False

    def test_verification_latency_under_5s(self, client):
        client.post("/manifests", json=self.MANIFEST)
        start = time.perf_counter()
        for _ in range(50):
            client.get(f"/manifests/{self.MANIFEST['manifest_id']}/verify")
        avg = time.perf_counter() - start
        assert avg < 5.0  # 50 verifications, SLO is < 5 s per single vehicle
