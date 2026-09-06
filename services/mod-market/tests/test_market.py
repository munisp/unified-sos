"""mod-market: registry, stallage ticketing, disputes."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

MARKET = {
    "market_id": "MKT-OS-OSOGBO-CENTRAL",
    "state_id": "osun",
    "name": "Osogbo Central Market",
    "market_type": "DAILY_MARKET",
    "lga": "Osogbo",
    "stall_capacity": 2,
}

STALL = {
    "stall_id": "STALL-OSO-A-014",
    "market_id": MARKET["market_id"],
    "block": "A",
    "number": "014",
    "daily_fee_kobo": 20_000,
}

TRADER = {
    "trader_id": "TRD-OS-000123",
    "state_id": "osun",
    "full_name": "Adaeze Okonkwo",
    "phone": "+2348012345678",
    "market_id": MARKET["market_id"],
    "stall_id": STALL["stall_id"],
}


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def setup_market(client):
    assert client.post("/markets", json=MARKET).status_code == 201
    assert client.post("/stalls", json=STALL).status_code == 201
    assert client.post("/traders", json=TRADER).status_code == 201


class TestRegistry:
    def test_market_stall_trader_registration(self, client):
        setup_market(client)
        traders = client.get("/traders", params={"market_id": MARKET["market_id"]}).json()
        assert len(traders) == 1
        assert traders[0]["trader_id"] == TRADER["trader_id"]

    def test_stall_requires_known_market(self, client):
        assert client.post("/stalls", json=STALL).status_code == 404

    def test_stall_capacity_enforced(self, client):
        client.post("/markets", json=MARKET)
        client.post("/stalls", json=STALL)
        client.post("/stalls", json=dict(STALL, stall_id="STALL-OSO-A-015", number="015"))
        r = client.post("/stalls", json=dict(STALL, stall_id="STALL-OSO-A-016", number="016"))
        assert r.status_code == 409  # capacity = 2


class TestStallageTicketing:
    def test_issue_daily_ticket(self, client):
        setup_market(client)
        r = client.post(
            "/tickets",
            json={
                "stall_id": STALL["stall_id"],
                "service_date": "2026-03-02",
                "trader_id": TRADER["trader_id"],
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["amount_kobo"] == STALL["daily_fee_kobo"]
        assert body["transfer_code"] == 130
        assert body["status"] == "PAID"
        assert body["origin"] == "ONLINE"

    def test_double_charge_same_day_rejected(self, client):
        setup_market(client)
        payload = {"stall_id": STALL["stall_id"], "service_date": "2026-03-02"}
        assert client.post("/tickets", json=payload).status_code == 201
        assert client.post("/tickets", json=payload).status_code == 409
        # next day is fine
        ok = client.post(
            "/tickets", json={"stall_id": STALL["stall_id"], "service_date": "2026-03-03"}
        )
        assert ok.status_code == 201


class TestDisputeWorkflow:
    def _ticket(self, client) -> str:
        setup_market(client)
        r = client.post(
            "/tickets",
            json={"stall_id": STALL["stall_id"], "service_date": "2026-03-02"},
        )
        return r.json()["ticket_id"]

    def test_full_dispute_trail_is_append_only(self, client):
        tid = self._ticket(client)
        events = [
            {"action": "OPENED", "actor_id": TRADER["trader_id"], "note": "charged twice"},
            {"action": "EVIDENCE_ATTACHED", "actor_id": TRADER["trader_id"], "evidence_ref": "USSD-REF-991"},
            {"action": "ESCALATED_TO_ARBITRATION", "actor_id": "MM-OSO-01"},
            {"action": "RESOLVED_REFUNDED", "actor_id": "ARB-OS-04", "note": "double charge confirmed"},
        ]
        for i, ev in enumerate(events):
            r = client.post(
                "/disputes/events",
                json={"event_id": f"DEV-{i:03d}", "ticket_id": tid, **ev},
            )
            assert r.status_code == 201, r.text

        trail = client.get(f"/disputes/{tid}/trail").json()
        assert [e["action"] for e in trail] == [e["action"] for e in events]
        assert client.get(f"/tickets/{tid}").json()["status"] == "RESOLVED_REFUNDED"
        # append-only: trail is exactly what was posted, in order, retrievable
        assert len(trail) == 4

    def test_cannot_resolve_unopened_dispute(self, client):
        tid = self._ticket(client)
        r = client.post(
            "/disputes/events",
            json={
                "event_id": "DEV-X",
                "ticket_id": tid,
                "action": "RESOLVED_UPHELD",
                "actor_id": "ARB-1",
            },
        )
        assert r.status_code == 409

    def test_cannot_reopen_resolved_ticket(self, client):
        tid = self._ticket(client)
        client.post(
            "/disputes/events",
            json={"event_id": "D1", "ticket_id": tid, "action": "OPENED", "actor_id": "T"},
        )
        client.post(
            "/disputes/events",
            json={"event_id": "D2", "ticket_id": tid, "action": "RESOLVED_UPHELD", "actor_id": "A"},
        )
        r = client.post(
            "/disputes/events",
            json={"event_id": "D3", "ticket_id": tid, "action": "OPENED", "actor_id": "T"},
        )
        assert r.status_code == 409
