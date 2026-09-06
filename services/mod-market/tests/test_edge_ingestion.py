"""Offline stallage ingestion consumes the real edge-daemon batch format.

These tests generate genuine signed batches using ``edge/edge-daemon`` so the
wire contract between POS collectors and mod-market is verified end-to-end.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

_EDGE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "edge", "edge-daemon")
)
sys.path.insert(0, _EDGE_DIR)

from edge_daemon.crypto import DeviceSigner  # noqa: E402
from edge_daemon.daemon import EdgeDaemon  # noqa: E402
from edge_daemon.models import RevenueTicketPayload, SyncBatch  # noqa: E402

from app.main import create_app  # noqa: E402

DEVICE = "POS-OS-OSOGBO-011"

MARKET = {
    "market_id": "MKT-OS-OSOGBO-CENTRAL",
    "state_id": "osun",
    "name": "Osogbo Central Market",
    "market_type": "DAILY_MARKET",
    "lga": "Osogbo",
    "stall_capacity": 100,
}

STALL = {
    "stall_id": "STALL-OSO-A-014",
    "market_id": MARKET["market_id"],
    "block": "A",
    "number": "014",
    "daily_fee_kobo": 20_000,
}


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        assert c.post("/markets", json=MARKET).status_code == 201
        assert c.post("/stalls", json=STALL).status_code == 201
        yield c


def make_batch(tmp_path, tickets):
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=DeviceSigner.generate(DEVICE))
    for payload in tickets:
        daemon.issue(payload)
    records = daemon.outbox.pending(limit=500)
    daemon.close()
    return SyncBatch(device_id=DEVICE, records=records)


def stallage_payload(stall_id: str, bill_ref: str) -> RevenueTicketPayload:
    return RevenueTicketPayload(
        state_id="osun",
        bill_reference=bill_ref,
        payer_id="TRD-OS-000123",
        levy_code="130",
        amount_kobo=20_000,
        collector_id="AGENT-OS-7",
        location=stall_id,
    )


class TestEdgeBatchIngestion:
    def test_signed_offline_batch_accepted(self, client, tmp_path):
        batch = make_batch(tmp_path, [stallage_payload(STALL["stall_id"], "BR-OS-1")])
        r = client.post("/tickets/ingest-edge-batch", json=batch.model_dump(mode="json"))
        assert r.status_code == 200, r.text
        acks = r.json()
        assert acks[0]["status"] == "accepted"
        ticket = client.get(f"/tickets/{acks[0]['ticket_id']}").json()
        assert ticket["origin"] == "OFFLINE_EDGE"
        assert ticket["edge_device_id"] == DEVICE
        assert ticket["edge_sequence"] == 1
        assert ticket["edge_signature"]

    def test_replay_is_idempotent(self, client, tmp_path):
        batch = make_batch(tmp_path, [stallage_payload(STALL["stall_id"], "BR-OS-1")])
        body = batch.model_dump(mode="json")
        first = client.post("/tickets/ingest-edge-batch", json=body).json()
        second = client.post("/tickets/ingest-edge-batch", json=body).json()
        assert first[0]["status"] == "accepted"
        assert second[0]["status"] == "duplicate"
        assert second[0]["ticket_id"] == first[0]["ticket_id"]

    def test_forged_signature_rejected(self, client, tmp_path):
        batch = make_batch(tmp_path, [stallage_payload(STALL["stall_id"], "BR-OS-1")])
        body = batch.model_dump(mode="json")
        body["records"][0]["signature"] = body["records"][0]["signature"][:-4] + "AAAA"
        acks = client.post("/tickets/ingest-edge-batch", json=body).json()
        assert acks[0]["status"] == "rejected"
        assert "signature" in acks[0]["detail"]

    def test_wrong_levy_code_rejected(self, client, tmp_path):
        payload = stallage_payload(STALL["stall_id"], "BR-OS-1").model_copy(
            update={"levy_code": "110"}  # mineral levy, not stallage
        )
        batch = make_batch(tmp_path, [payload])
        acks = client.post(
            "/tickets/ingest-edge-batch", json=batch.model_dump(mode="json")
        ).json()
        assert acks[0]["status"] == "rejected"
        assert "levy code" in acks[0]["detail"]

    def test_unknown_stall_rejected(self, client, tmp_path):
        batch = make_batch(tmp_path, [stallage_payload("STALL-GHOST", "BR-OS-9")])
        acks = client.post(
            "/tickets/ingest-edge-batch", json=batch.model_dump(mode="json")
        ).json()
        assert acks[0]["status"] == "rejected"
        assert "stall" in acks[0]["detail"]

    def test_same_stall_same_day_deduped_not_double_charged(self, client, tmp_path):
        # Two different offline devices collect the same stall/day: second is
        # acked as duplicate — trader never double-charged.
        (tmp_path / "d1").mkdir()
        (tmp_path / "d2").mkdir()
        batch1 = make_batch(tmp_path / "d1", [stallage_payload(STALL["stall_id"], "BR-1")])
        daemon2 = EdgeDaemon(
            tmp_path / "d2" / "edge.db", "POS-OS-OSOGBO-012",
            signer=DeviceSigner.generate("POS-OS-OSOGBO-012"),
        )
        daemon2.issue(stallage_payload(STALL["stall_id"], "BR-2"))
        batch2 = SyncBatch(device_id="POS-OS-OSOGBO-012", records=daemon2.outbox.pending())
        daemon2.close()

        a1 = client.post(
            "/tickets/ingest-edge-batch", json=batch1.model_dump(mode="json")
        ).json()
        a2 = client.post(
            "/tickets/ingest-edge-batch", json=batch2.model_dump(mode="json")
        ).json()
        assert a1[0]["status"] == "accepted"
        assert a2[0]["status"] == "duplicate"
        assert a2[0]["ticket_id"] == a1[0]["ticket_id"]
