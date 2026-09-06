"""mod-mining: lifecycle, levy computation, event contract, royalty constraint."""
import pytest
from fastapi.testclient import TestClient

from app.bus import InMemoryEventBus
from app.levy import (
    DEFAULT_POLICY,
    RoyaltyConstraintViolation,
    SplitRule,
    assess_levy,
)
from app.main import create_app
from app.models import MineralType

SITE = {
    "site_id": "SITE-NAS-KOKO-01",
    "state_id": "nasarawa",
    "mine_lease_id": "ML-NAS-KOKO-04",
    "operator_name": "Koko Lithium Ltd",
    "minerals": ["LITHIUM_SPODUMENE", "TANTALITE"],
}

CONSIGNMENT = {
    "consignment_id": "MIN-NAS-2026-0819",
    "state_id": "nasarawa",
    "site_id": SITE["site_id"],
    "mine_lease_id": "ML-NAS-KOKO-04",
    "mineral_type": "LITHIUM_SPODUMENE",
    "truck_registration": "NSR-412-XA",
    "rfid_seal_id": "RFID-881290-09",
    "destination_corridor": "PORT_LAGOS_APAPA",
}

WEIGHBRIDGE = {
    "station_id": "WB-KOKO-01",
    "gross_weight_kg": 32_450.0,
    "tare_weight_kg": 12_100.0,
    "axle_weights_kg": [8_100, 8_050, 8_200, 8_100],
    "anpr_plate": "NSR-412-XA",
}

ASSAY = {"assay_id": "ASY-0001", "lab_id": "LAB-JOS-02", "lithium_oxide_grade_pct": 5.85}


@pytest.fixture
def client():
    bus = InMemoryEventBus()
    app = create_app(bus=bus)
    with TestClient(app) as c:
        c.state_bus = bus  # type: ignore[attr-defined]
        yield c


def full_dispatch(client) -> dict:
    assert client.post("/sites", json=SITE).status_code == 201
    assert client.post("/consignments", json=CONSIGNMENT).status_code == 201
    r = client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/weighbridge", json=WEIGHBRIDGE)
    assert r.status_code == 200, r.text
    r = client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/assay", json=ASSAY)
    assert r.status_code == 200, r.text
    r = client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/dispatch")
    assert r.status_code == 200, r.text
    return r.json()


class TestLifecycle:
    def test_full_lifecycle_create_weigh_assay_dispatch_deliver(self, client):
        dispatched = full_dispatch(client)
        assert dispatched["status"] == "DISPATCHED"
        assert dispatched["levy"]["total_kobo"] > 0

        r = client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/deliver")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "DELIVERED"
        assert body["delivered_at"] is not None

    def test_dispatch_requires_weighbridge(self, client):
        client.post("/sites", json=SITE)
        client.post("/consignments", json=CONSIGNMENT)
        r = client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/dispatch")
        assert r.status_code == 409

    def test_weighbridge_rejects_impossible_weights(self, client):
        client.post("/sites", json=SITE)
        client.post("/consignments", json=CONSIGNMENT)
        bad = dict(WEIGHBRIDGE, tare_weight_kg=40_000.0)  # tare > gross
        r = client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/weighbridge", json=bad)
        assert r.status_code == 409

    def test_lithium_requires_assay_grade(self, client):
        client.post("/sites", json=SITE)
        client.post("/consignments", json=CONSIGNMENT)
        client.post(f"/consignments/{CONSIGNMENT['consignment_id']}/weighbridge", json=WEIGHBRIDGE)
        r = client.post(
            f"/consignments/{CONSIGNMENT['consignment_id']}/assay",
            json={"assay_id": "A", "lab_id": "L"},
        )
        assert r.status_code == 409  # Li consignment without Li2O grade

    def test_unlicensed_mineral_rejected(self, client):
        client.post("/sites", json=SITE)
        bad = dict(CONSIGNMENT, mineral_type="GOLD_ORE")
        assert client.post("/consignments", json=bad).status_code == 422

    def test_unknown_site_rejected(self, client):
        assert client.post("/consignments", json=CONSIGNMENT).status_code == 404


class TestLevy:
    def test_levy_matches_tonnage_plus_grade_uplift(self, client):
        body = full_dispatch(client)
        net_kg = 32_450.0 - 12_100.0  # 20,350 kg
        base = int(net_kg * DEFAULT_POLICY.rate_kobo_per_kg[MineralType.LITHIUM_SPODUMENE])
        expected = base + int(base * 5.85 * DEFAULT_POLICY.lithium_grade_uplift_bps_per_pct / 10_000)
        assert body["levy"]["total_kobo"] == expected
        # split lines sum exactly to the total (no dust)
        assert sum(l["amount_kobo"] for l in body["levy"]["lines"]) == expected
        # all lines are state-competent: transfer code 110, class 1xxx-4xxx
        for line in body["levy"]["lines"]:
            assert line["transfer_code"] == 110
            assert line["tigerbeetle_account_code"] < 5000

    def test_federal_royalty_kept_separate(self, client):
        body = full_dispatch(client)
        levy = body["levy"]
        assert levy["federal_royalty_reference_kobo"] == int(
            (32_450.0 - 12_100.0) * DEFAULT_POLICY.federal_royalty_rate_kobo_per_kg
        )
        # informational only — never part of the assessed state total
        assert levy["federal_royalty_reference_kobo"] not in (
            l["amount_kobo"] for l in levy["lines"]
        )


class TestRoyaltyHardConstraint:
    @pytest.mark.parametrize(
        "beneficiary,code",
        [
            ("FEDERAL_ROYALTY_ACCOUNT", 3001),
            ("State Royalty Pool", 3001),
            ("FEDERATION_ACCOUNT", 3001),
            ("STATE_CONSOLIDATED_REVENUE_FUND", 5001),  # 5xxx pass-through
            ("STATE_CONSOLIDATED_REVENUE_FUND", 5400),
        ],
    )
    def test_split_rules_claiming_royalty_share_rejected(self, beneficiary, code):
        with pytest.raises(RoyaltyConstraintViolation):
            SplitRule(beneficiary=beneficiary, tigerbeetle_account_code=code, share_bps=5_000)

    def test_valid_state_split_rule_accepted(self):
        rule = SplitRule(
            beneficiary="STATE_CONSOLIDATED_REVENUE_FUND",
            tigerbeetle_account_code=3001,
            share_bps=8_000,
        )
        assert rule.share_bps == 8_000

    def test_api_rejects_royalty_split_rule(self, client):
        r = client.post(
            "/split-rules/validate",
            json={
                "beneficiary": "FEDERAL_ROYALTY_ACCOUNT",
                "tigerbeetle_account_code": 3001,
                "share_bps": 1_000,
            },
        )
        assert r.status_code == 422

    def test_api_accepts_state_split_rule(self, client):
        r = client.post(
            "/split-rules/validate",
            json={
                "beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND",
                "tigerbeetle_account_code": 3001,
                "share_bps": 10_000,
            },
        )
        assert r.status_code == 200


class TestEvents:
    def test_dispatch_publishes_contract_event(self, client):
        full_dispatch(client)
        events = client.get("/events").json()
        assert len(events) == 1
        evt = events[0]
        assert evt["topic"] == "ng.sos.mining.consignment_dispatched"
        payload = evt["payload"]
        # required fields per contracts/asyncapi/mining-events.yaml
        for field_name in (
            "state_id",
            "consignment_id",
            "mine_lease_id",
            "mineral_type",
            "net_weight_kg",
            "royalty_due_kobo",
            "timestamp",
        ):
            assert field_name in payload
        assert payload["consignment_id"] == "MIN-NAS-2026-0819"
        assert payload["net_weight_kg"] == 20_350.0
        assert payload["rfid_seal_id"] == "RFID-881290-09"

    def test_consumer_subscription_receives_dispatch(self, client):
        received = []
        client.state_bus.subscribe("ng.sos.mining.consignment_dispatched", received.append)
        full_dispatch(client)
        assert len(received) == 1
        assert received[0].royalty_due_kobo > 0
