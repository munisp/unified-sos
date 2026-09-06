"""mod-forestry: tags, provenance, NDVI alerts, stumpage billing, untagged haulage."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import ForestryService

TAG = {
    "tag_id": "RFID-TAR-000123",
    "state_id": "taraba",
    "species": "ROSEWOOD",
    "coupe_id": "COUPE-GASHAKA-07",
    "licensee_id": "LIC-TAR-4410",
    "volume_m3": 4.2,
}


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def register_tag(client):
    r = client.post("/tags", json=TAG)
    assert r.status_code == 201, r.text
    return r.json()


def provenance_event(tag_id, stage, **kw):
    base = {
        "event_id": f"EVT-{stage}",
        "tag_id": tag_id,
        "stage": stage,
        "actor_id": "RANGER-9",
        "location": TAG["coupe_id"],
        "gps_lat": 7.31,
        "gps_lon": 11.21,
    }
    base.update(kw)
    return base


class TestTagRegistry:
    def test_register_and_get(self, client):
        register_tag(client)
        r = client.get(f"/tags/{TAG['tag_id']}")
        assert r.status_code == 200
        assert r.json()["status"] == "ISSUED"

    def test_duplicate_tag_rejected(self, client):
        register_tag(client)
        assert client.post("/tags", json=TAG).status_code == 409

    def test_unknown_tag_404(self, client):
        assert client.get("/tags/NOPE").status_code == 404

    def test_list_by_state(self, client):
        register_tag(client)
        assert len(client.get("/tags", params={"state_id": "taraba"}).json()) == 1
        assert len(client.get("/tags", params={"state_id": "ogun"}).json()) == 0


class TestProvenanceChain:
    def test_full_chain_harvest_transit_mill(self, client):
        register_tag(client)
        r = client.post("/provenance", json=provenance_event(TAG["tag_id"], "HARVESTED"))
        assert r.status_code == 201, r.text
        r = client.post(
            "/provenance",
            json=provenance_event(
                TAG["tag_id"], "IN_TRANSIT", transit_permit_id="TTP-TAR-2026-011"
            ),
        )
        assert r.status_code == 201
        r = client.post(
            "/provenance",
            json=provenance_event(TAG["tag_id"], "MILLED", location="MILL-MUTUM-BIYU-1"),
        )
        assert r.status_code == 201

        chain = client.get(f"/tags/{TAG['tag_id']}/provenance").json()
        assert [e["stage"] for e in chain] == ["HARVESTED", "IN_TRANSIT", "MILLED"]
        assert client.get(f"/tags/{TAG['tag_id']}").json()["status"] == "MILLED"

    def test_illegal_transition_rejected(self, client):
        register_tag(client)
        # cannot mill straight from ISSUED
        r = client.post(
            "/provenance",
            json=provenance_event(TAG["tag_id"], "MILLED", location="MILL-1"),
        )
        assert r.status_code == 409

    def test_transit_requires_permit(self, client):
        register_tag(client)
        client.post("/provenance", json=provenance_event(TAG["tag_id"], "HARVESTED"))
        r = client.post("/provenance", json=provenance_event(TAG["tag_id"], "IN_TRANSIT"))
        assert r.status_code == 409

    def test_harvest_outside_licensed_coupe_rejected(self, client):
        register_tag(client)
        r = client.post(
            "/provenance",
            json=provenance_event(TAG["tag_id"], "HARVESTED", location="COUPE-UNLICENSED-99"),
        )
        assert r.status_code == 409

    def test_milled_chain_is_closed(self, client):
        register_tag(client)
        client.post("/provenance", json=provenance_event(TAG["tag_id"], "HARVESTED"))
        client.post(
            "/provenance",
            json=provenance_event(TAG["tag_id"], "IN_TRANSIT", transit_permit_id="T"),
        )
        client.post(
            "/provenance",
            json=provenance_event(TAG["tag_id"], "MILLED", location="MILL-1"),
        )
        r = client.post(
            "/provenance",
            json=provenance_event(TAG["tag_id"], "IN_TRANSIT", transit_permit_id="T2"),
        )
        assert r.status_code == 409


class TestStumpageBilling:
    def test_billing_hook_fires_on_harvest(self, client):
        register_tag(client)
        client.post("/provenance", json=provenance_event(TAG["tag_id"], "HARVESTED"))
        invoices = client.get("/invoices", params={"tag_id": TAG["tag_id"]}).json()
        assert len(invoices) == 1
        inv = invoices[0]
        assert inv["species"] == "ROSEWOOD"
        assert inv["volume_m3"] == 4.2
        assert inv["amount_kobo"] == int(4.2 * 85_000_00)
        assert inv["transfer_code"] == 110

    def test_manual_bill_requires_volume(self, client):
        no_vol = dict(TAG, tag_id="RFID-TAR-000124")
        del no_vol["volume_m3"]
        client.post("/tags", json=no_vol)
        r = client.post(f"/tags/{no_vol['tag_id']}/stumpage")
        assert r.status_code == 409


class TestDeforestationAlerts:
    ALERT = {
        "alert_id": "NDVI-2026-0341",
        "state_id": "taraba",
        "coupe_id": "COUPE-GASHAKA-07",
        "disturbed_area_ha": 1.7,
        "ndvi_drop": 0.42,
        "gps_lat": 7.30,
        "gps_lon": 11.22,
        "detected_at": "2026-09-01T04:00:00Z",
    }

    def test_ingest_and_filter_actionable(self, client):
        r = client.post("/alerts/deforestation", json=self.ALERT)
        assert r.status_code == 201
        assert r.json()["severity"] == "ACTIONABLE"  # > 0.5 ha threshold

        small = dict(self.ALERT, alert_id="NDVI-2026-0342", disturbed_area_ha=0.3)
        assert client.post("/alerts/deforestation", json=small).status_code == 201

        all_alerts = client.get("/alerts/deforestation").json()
        assert len(all_alerts) == 2
        actionable = client.get(
            "/alerts/deforestation", params={"actionable_only": True}
        ).json()
        assert [a["alert_id"] for a in actionable] == ["NDVI-2026-0341"]

    def test_alert_ingested_within_72h_budget(self, client):
        recent = dict(
            self.ALERT,
            alert_id="NDVI-2026-0399",
            detected_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        r = client.post("/alerts/deforestation", json=recent)
        body = r.json()
        detected = datetime.fromisoformat(body["detected_at"].replace("Z", "+00:00"))
        ingested = datetime.fromisoformat(body["ingested_at"].replace("Z", "+00:00"))
        assert (ingested - detected).total_seconds() <= 72 * 3600

    def test_severity_critical(self, client):
        big = dict(self.ALERT, alert_id="NDVI-2026-0350", disturbed_area_ha=6.0)
        assert client.post("/alerts/deforestation", json=big).json()["severity"] == "CRITICAL"


class TestUntaggedHaulage:
    def test_report_publishes_event(self):
        published = []

        class Bus:
            def publish(self, topic, payload):
                published.append((topic, payload))

        app = create_app(ForestryService(bus=Bus()))
        with TestClient(app) as client:
            r = client.post(
                "/alerts/untagged-haulage",
                json={
                    "state_id": "taraba",
                    "checkpoint_id": "CHK-GEMBU-01",
                    "vehicle_plate": "TAR-88-KT",
                    "gps": {"lat": 6.99, "lon": 11.18},
                },
            )
            assert r.status_code == 201
            assert len(published) == 1
            topic, payload = published[0]
            assert topic == "ng.sos.forestry.untagged_timber_alert"
            assert payload.vehicle_plate == "TAR-88-KT"
