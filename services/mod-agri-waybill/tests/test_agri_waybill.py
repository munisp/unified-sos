"""mod-agri-waybill: issuance, QR verify, tracking, levy, warehouse receipts."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

WAYBILL = {
    "waybill_number": "WB-BEN-2026-000123",
    "state_id": "benue",
    "consignor_id": "FARM-COOP-GBOKO-12",
    "consignee_id": "MKT-LAG-MILE12",
    "produce_type": "YAM_TUBERS",
    "quantity_kg": 8_200.0,
    "vehicle_plate": "BEN-221-ZX",
    "origin": "GBOKO",
    "destination": "LAGOS_MILE12",
    "levy_kobo": 250_000,
}


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def issue(client):
    r = client.post("/waybills", json=WAYBILL)
    assert r.status_code == 201, r.text
    return r.json()


class TestIssuance:
    def test_issue_returns_qr_payload_and_levy(self, client):
        body = issue(client)
        assert body["status"] == "ISSUED"
        assert body["qr_payload"].startswith("SOSWB1.")
        assert body["levy_kobo"] == 250_000
        assert body["transfer_code"] == 140

    def test_duplicate_waybill_rejected(self, client):
        issue(client)
        assert client.post("/waybills", json=WAYBILL).status_code == 409


class TestQRVerification:
    def test_valid_qr_verifies(self, client):
        qr = issue(client)["qr_payload"]
        r = client.post("/waybills/verify", json={"qr_payload": qr})
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is True
        assert body["waybill_number"] == WAYBILL["waybill_number"]
        assert body["status"] == "ISSUED"

    def test_tampered_qr_rejected(self, client):
        qr = issue(client)["qr_payload"]
        version, raw, sig = qr.split(".")
        tampered = f"{version}.{raw}.{sig[:-4]}AAAA"
        r = client.post("/waybills/verify", json={"qr_payload": tampered})
        assert r.json()["valid"] is False

    def test_malformed_qr_rejected(self, client):
        for bad in ("", "not-a-payload", "SOSWB0.xx.yy"):
            r = client.post("/waybills/verify", json={"qr_payload": bad})
            assert r.json()["valid"] is False

    def test_unknown_waybill_qr_rejected(self, client):
        # valid signature format but the waybill was never issued: craft via a
        # second service instance (different store, same dev key)
        other = create_app()
        with TestClient(other) as c2:
            qr = c2.post("/waybills", json=WAYBILL).json()["qr_payload"]
        r = client.post("/waybills/verify", json={"qr_payload": qr})
        body = r.json()
        assert body["valid"] is False
        assert "not registered" in body["detail"]

    def test_verify_latency_sanity(self, client):
        qr = issue(client)["qr_payload"]
        start = time.perf_counter()
        for _ in range(50):
            client.post("/waybills/verify", json={"qr_payload": qr})
        avg = (time.perf_counter() - start) / 50
        assert avg < 1.0, f"avg verify {avg*1000:.0f}ms (target well under 10 s)"


class TestTracking:
    def test_checkpoint_events_and_status_flow(self, client):
        issue(client)
        ev = {
            "event_id": "TRK-1",
            "waybill_number": WAYBILL["waybill_number"],
            "checkpoint_id": "CHK-LOKOJA-01",
            "gps_lat": 7.8,
            "gps_lon": 6.7,
        }
        assert client.post(
            f"/waybills/{WAYBILL['waybill_number']}/tracking", json=ev
        ).status_code == 201
        assert client.get(f"/waybills/{WAYBILL['waybill_number']}").json()["status"] == "IN_TRANSIT"
        trail = client.get(f"/waybills/{WAYBILL['waybill_number']}/tracking").json()
        assert len(trail) == 1

    def test_tracking_after_delivery_rejected(self, client):
        issue(client)
        client.post(f"/waybills/{WAYBILL['waybill_number']}/deliver")
        ev = {
            "event_id": "TRK-2",
            "waybill_number": WAYBILL["waybill_number"],
            "checkpoint_id": "CHK-X",
        }
        r = client.post(f"/waybills/{WAYBILL['waybill_number']}/tracking", json=ev)
        assert r.status_code == 409


class TestWarehouseReceipts:
    RECEIPT = {
        "receipt_id": "WHR-NAS-2026-0007",
        "state_id": "nasarawa",
        "warehouse_id": "WH-LAFIA-HUB-2",
        "waybill_number": WAYBILL["waybill_number"],
        "depositor_id": "FARM-COOP-GBOKO-12",
        "produce_type": "YAM_TUBERS",
        "quantity_kg": 8_000.0,
        "grade": "A",
        "storage_location": "SILO-4",
    }

    def test_receipt_requires_delivered_consignment(self, client):
        issue(client)
        r = client.post("/warehouse-receipts", json=self.RECEIPT)
        assert r.status_code == 409  # not delivered yet
        client.post(f"/waybills/{WAYBILL['waybill_number']}/deliver")
        r = client.post("/warehouse-receipts", json=self.RECEIPT)
        assert r.status_code == 201
        assert r.json()["collateralized"] is False

    def test_receipt_cannot_exceed_consignment_quantity(self, client):
        issue(client)
        client.post(f"/waybills/{WAYBILL['waybill_number']}/deliver")
        bad = dict(self.RECEIPT, receipt_id="WHR-2", quantity_kg=9_000.0)
        assert client.post("/warehouse-receipts", json=bad).status_code == 409

    def test_receipt_produce_type_must_match(self, client):
        issue(client)
        client.post(f"/waybills/{WAYBILL['waybill_number']}/deliver")
        bad = dict(self.RECEIPT, receipt_id="WHR-3", produce_type="RICE_PADDY")
        assert client.post("/warehouse-receipts", json=bad).status_code == 409
