"""Tests: fail-closed title verification + foreclosure pipeline.

Covers: title snapshot match/mismatch/owner-mismatch/encumbrance lien
registration; FORECLOSED → POSSESSION_REGISTERED → SALE_AUTHORIZED → SOLD →
PROCEEDS_DISTRIBUTED → CLOSED with integer-kobo waterfall conservation;
deficiency; below-reserve rejection; title-transfer failure blocking the
sale; illegal stage transitions; tenant isolation.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from mortgage_app import events as ev
from mortgage_app.adapters import (
    FixtureLandRegistry,
    TitleSnapshot,
    deterministic_transfer_id,
)
from mortgage_app.domain import MortgageStatus
from mortgage_app.main import create_app

from conftest import (
    BASE,
    HEADERS,
    apply_payload,
    make_now,
    run_to_approved,
    run_to_disbursed,
    run_to_lien,
)

OTHER = {"X-State-Tenant": "ogun"}


def fc_client(bus, make_store, ledger=None):
    """Client with a mutable clock (needed to drive DEFAULTED)."""
    now = [make_now()]
    store = make_store(clock=lambda: now[0])
    return TestClient(create_app(store=store, bus=bus)), now


def run_to_foreclosed(client, now, **over) -> str:
    mid = run_to_disbursed(client, **over)
    now[0] = now[0] + timedelta(days=200)  # >90 days past first due date
    r = client.post(f"{BASE}/{mid}/foreclose",
                    json={"reason": "90+ days arrears"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["mortgage"]["status"] == "FORECLOSED"
    return mid


def run_to_authorized(client, now, mid, valuation=2_000_000, reserve=1_000_000):
    r = client.post(f"{BASE}/{mid}/possession",
                    json={"reference": "court-order-42"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    r = client.post(f"{BASE}/{mid}/authorize-sale",
                    json={"valuation_kobo": valuation,
                          "reserve_price_kobo": reserve,
                          "valuer_id": "valuer-7"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return mid


def outstanding_total(client, mid) -> int:
    return client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]


# --- title verification at lien registration ----------------------------------
class TestTitleVerification:
    def test_title_match_registers_with_hash(self, client):
        mid = run_to_lien(client)
        lien = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                          headers=HEADERS).json()[0]
        assert lien["mortgage_id"] == mid
        assert lien["title_hash"]  # verified title snapshot hash recorded

    def test_title_mismatch_409(self, client):
        mid = run_to_approved(client)
        lands = client.app.state.store.lands
        lands.register("parcel-001", "LAG-2024-999999", "applicant-001")
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409
        assert "mismatch" in r.json()["detail"]

    def test_owner_mismatch_409(self, client):
        mid = run_to_approved(client)
        lands = client.app.state.store.lands
        lands.register("parcel-001", "LAG-2024-000123", "someone-else")
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409
        assert "owner" in r.json()["detail"]

    def test_blocking_encumbrance_409(self, client):
        mid = run_to_approved(client)
        lands = client.app.state.store.lands
        lands.register("parcel-001", "LAG-2024-000123", "applicant-001",
                       has_blocking_encumbrance=True)
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409
        assert "encumbrance" in r.json()["detail"]

    def test_non_current_title_status_409(self, client):
        mid = run_to_approved(client)
        lands = client.app.state.store.lands
        lands.register("parcel-001", "LAG-2024-000123", "applicant-001",
                       status="revoked")
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409

    def test_strict_adapter_fails_closed_on_unverified(self, bus, make_store):
        class StrictUnverifiedRegistry:
            strict = True

            def title_snapshot(self, state_id, parcel_id):
                return TitleSnapshot(status="unverified", verified=False)

        store = make_store()
        store.lands = StrictUnverifiedRegistry()
        client = TestClient(create_app(store=store, bus=bus))
        r = client.post(BASE + "/", json=apply_payload(), headers=HEADERS)
        mid = r.json()["mortgage"]["mortgage_id"]
        client.post(f"{BASE}/{mid}/credit-review", headers=HEADERS)
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409
        assert "UNVERIFIED" in r.json()["detail"]

    def test_fixture_lenient_on_unknown_parcel(self, client):
        # fixture (strict=False) allows an unattested parcel; title_hash of
        # the UNVERIFIED snapshot is still recorded on the lien
        store = client.app.state.store
        store.lands._parcels.clear()
        mid = run_to_approved(client)
        store.lands._parcels.clear()  # undo conftest seeding
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 201, r.text


# --- possession & sale authorization -------------------------------------------
class TestPossessionAndAuthorization:
    def test_possession_happy(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        r = client.post(f"{BASE}/{mid}/possession",
                        json={"reference": "consent-gov-1"}, headers=HEADERS)
        assert r.status_code == 200
        m = r.json()["mortgage"]
        assert m["status"] == "POSSESSION_REGISTERED"
        assert m["possession_reference"] == "consent-gov-1"
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_POSSESSION_REGISTERED in topics

    def test_possession_requires_reference_422(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        r = client.post(f"{BASE}/{mid}/possession",
                        json={"reference": ""}, headers=HEADERS)
        assert r.status_code == 422

    def test_possession_wrong_stage_409(self, client):
        mid = run_to_disbursed(client)
        r = client.post(f"{BASE}/{mid}/possession",
                        json={"reference": "x"}, headers=HEADERS)
        assert r.status_code == 409

    def test_authorize_sale_happy(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid, valuation=3_000_000, reserve=1_500_000)
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["status"] == "SALE_AUTHORIZED"
        assert m["valuation_kobo"] == 3_000_000
        assert m["reserve_price_kobo"] == 1_500_000
        assert m["valuer_id"] == "valuer-7"
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_SALE_AUTHORIZED in topics

    def test_authorize_sale_requires_valuer_422(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        client.post(f"{BASE}/{mid}/possession",
                    json={"reference": "r"}, headers=HEADERS)
        r = client.post(f"{BASE}/{mid}/authorize-sale",
                        json={"valuation_kobo": 100, "reserve_price_kobo": 50,
                              "valuer_id": ""}, headers=HEADERS)
        assert r.status_code == 422

    def test_authorize_sale_reserve_above_valuation_422(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        client.post(f"{BASE}/{mid}/possession",
                    json={"reference": "r"}, headers=HEADERS)
        r = client.post(f"{BASE}/{mid}/authorize-sale",
                        json={"valuation_kobo": 50, "reserve_price_kobo": 100,
                              "valuer_id": "v"}, headers=HEADERS)
        assert r.status_code == 422

    def test_authorize_sale_wrong_stage_409(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)  # FORECLOSED, possession skipped
        r = client.post(f"{BASE}/{mid}/authorize-sale",
                        json={"valuation_kobo": 100, "reserve_price_kobo": 50,
                              "valuer_id": "v"}, headers=HEADERS)
        assert r.status_code == 409


# --- sale -----------------------------------------------------------------------
class TestSale:
    def test_sell_happy_transfers_title_and_escrows(self, bus, make_store, ledger):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid)
        r = client.post(f"{BASE}/{mid}/sell",
                        json={"purchaser_id": "buyer-1",
                              "gross_proceeds_kobo": 1_200_000,
                              "sale_costs_kobo": 50_000}, headers=HEADERS)
        assert r.status_code == 200, r.text
        m = r.json()["mortgage"]
        assert m["status"] == "SOLD"
        assert m["title_transfer_ref"]
        store = client.app.state.store
        tt = store.title_transfer.transfers[-1]
        assert tt["to_owner_id"] == "buyer-1"
        assert tt["parcel_id"] == "parcel-001"
        escrow = store.sale_escrow_account(mid)
        assert ledger.balance(escrow) == ledger._opening + 1_200_000
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_SOLD in topics

    def test_sell_below_reserve_rejected(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid, valuation=2_000_000, reserve=1_000_000)
        r = client.post(f"{BASE}/{mid}/sell",
                        json={"purchaser_id": "buyer-1",
                              "gross_proceeds_kobo": 999_999}, headers=HEADERS)
        assert r.status_code == 400
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["status"] == "SALE_AUTHORIZED"  # sale not recorded

    def test_sell_wrong_stage_409(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        r = client.post(f"{BASE}/{mid}/sell",
                        json={"purchaser_id": "b", "gross_proceeds_kobo": 1},
                        headers=HEADERS)
        assert r.status_code == 409

    def test_title_transfer_failure_blocks_sale(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        client.app.state.store  # keep reference
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid)
        store = client.app.state.store
        store.title_transfer.fail_next = True
        no_raise = TestClient(create_app(store=store, bus=bus),
                              raise_server_exceptions=False)
        r = no_raise.post(f"{BASE}/{mid}/sell",
                          json={"purchaser_id": "buyer-1",
                                "gross_proceeds_kobo": 1_200_000},
                          headers=HEADERS)
        assert r.status_code == 500  # fail-closed seam surfaces the error
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["status"] == "SALE_AUTHORIZED"
        assert not store.title_transfer.transfers  # no title moved


# --- proceeds distribution waterfall ---------------------------------------------
class TestWaterfall:
    def test_full_path_to_closed_conservation(self, bus, make_store, ledger):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid, valuation=2_000_000, reserve=100_000)
        senior_due = outstanding_total(client, mid)
        gross = senior_due + 150_000
        costs = 40_000
        client.post(f"{BASE}/{mid}/sell",
                    json={"purchaser_id": "buyer-1", "gross_proceeds_kobo": gross,
                          "sale_costs_kobo": costs}, headers=HEADERS)
        r = client.post(f"{BASE}/{mid}/distribute-proceeds", headers=HEADERS)
        assert r.status_code == 200, r.text
        m = r.json()["mortgage"]
        assert m["status"] == "CLOSED"
        d = m["distribution"]
        # waterfall: costs → senior → junior(0) → surplus; exact kobo
        assert d["costs_kobo"] == costs
        assert d["senior_kobo"] == senior_due
        assert d["junior_kobo"] == 0
        assert d["surplus_kobo"] == gross - costs - senior_due
        assert (d["costs_kobo"] + d["senior_kobo"] + d["junior_kobo"]
                + d["surplus_kobo"]) == gross  # conservation
        assert d["deficiency_kobo"] == 0
        # surplus landed on the borrower; costs on the costs account
        store = client.app.state.store
        borrower = store.applicant_account("applicant-001")
        assert ledger.balance(borrower) == ledger._opening + 600_000 + d["surplus_kobo"]
        # liens released at CLOSED
        lien = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                          headers=HEADERS).json()[0]
        assert lien["status"] == "RELEASED"
        topics = [e["topic"] for e in bus.published]
        for t in (ev.EVENT_PROCEEDS_DISTRIBUTED, ev.EVENT_CLOSED):
            assert t in topics
        assert r.json()["chain_valid"] is True

    def test_deterministic_distribution_transfer_ids(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid, valuation=2_000_000, reserve=100_000)
        senior_due = outstanding_total(client, mid)
        gross = senior_due + 10_000
        client.post(f"{BASE}/{mid}/sell",
                    json={"purchaser_id": "b", "gross_proceeds_kobo": gross,
                          "sale_costs_kobo": 5_000}, headers=HEADERS)
        m = client.post(f"{BASE}/{mid}/distribute-proceeds",
                        headers=HEADERS).json()["mortgage"]
        key = f"{mid}|foreclosure-distribution"
        ids = m["distribution"]["transfer_ids"]
        assert ids["costs"] == str(deterministic_transfer_id(key, "costs"))
        assert ids["senior"] == str(deterministic_transfer_id(key, "senior"))
        assert ids["surplus"] == str(deterministic_transfer_id(key, "surplus"))

    def test_junior_lien_paid_after_senior(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        # junior (second-charge) mortgage on the same parcel, disbursed
        senior_lien = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                                 headers=HEADERS).json()[0]
        mid2 = run_to_approved(client, applicant_id="applicant-002",
                               title_ref="LAG-2024-000124")
        r = client.post(f"{BASE}/{mid2}/register-lien",
                        json={"second_charge": True,
                              "senior_lien_id": senior_lien["lien_id"]},
                        headers=HEADERS)
        assert r.status_code == 201, r.text
        r = client.post(f"{BASE}/{mid2}/disburse", headers=HEADERS)
        assert r.status_code == 200, r.text
        junior_due = outstanding_total(client, mid2)
        run_to_authorized(client, now, mid)
        senior_due = outstanding_total(client, mid)
        gross = senior_due + junior_due + 25_000
        costs = 10_000
        client.post(f"{BASE}/{mid}/sell",
                    json={"purchaser_id": "b", "gross_proceeds_kobo": gross,
                          "sale_costs_kobo": costs}, headers=HEADERS)
        m = client.post(f"{BASE}/{mid}/distribute-proceeds",
                        headers=HEADERS).json()["mortgage"]
        d = m["distribution"]
        assert d["senior_kobo"] == senior_due
        assert d["junior_kobo"] == junior_due
        assert d["surplus_kobo"] == gross - costs - senior_due - junior_due
        assert (d["costs_kobo"] + d["senior_kobo"] + d["junior_kobo"]
                + d["surplus_kobo"]) == gross
        j = client.get(f"{BASE}/{mid2}", headers=HEADERS).json()["mortgage"]
        assert j["outstanding_principal_kobo"] == 0

    def test_deficiency_when_proceeds_insufficient(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        senior_due = outstanding_total(client, mid)
        run_to_authorized(client, now, mid, valuation=2_000_000,
                          reserve=50_000)
        gross = 60_000
        costs = 10_000
        client.post(f"{BASE}/{mid}/sell",
                    json={"purchaser_id": "b", "gross_proceeds_kobo": gross,
                          "sale_costs_kobo": costs}, headers=HEADERS)
        m = client.post(f"{BASE}/{mid}/distribute-proceeds",
                        headers=HEADERS).json()["mortgage"]
        d = m["distribution"]
        assert m["status"] == "CLOSED"
        assert d["costs_kobo"] == costs
        assert d["senior_kobo"] == gross - costs  # partial senior recovery
        assert d["surplus_kobo"] == 0
        assert d["deficiency_kobo"] == costs + senior_due - gross
        assert m["deficiency_kobo"] == d["deficiency_kobo"] > 0

    def test_distribute_wrong_stage_409(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        r = client.post(f"{BASE}/{mid}/distribute-proceeds", headers=HEADERS)
        assert r.status_code == 409  # FORECLOSED, not SOLD

    def test_distribute_twice_409(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        run_to_authorized(client, now, mid)
        client.post(f"{BASE}/{mid}/sell",
                    json={"purchaser_id": "b", "gross_proceeds_kobo": 1_500_000},
                    headers=HEADERS)
        assert client.post(f"{BASE}/{mid}/distribute-proceeds",
                           headers=HEADERS).status_code == 200
        r = client.post(f"{BASE}/{mid}/distribute-proceeds", headers=HEADERS)
        assert r.status_code == 409  # already CLOSED

    def test_tenant_isolation_foreclosure(self, bus, make_store):
        client, now = fc_client(bus, make_store)
        mid = run_to_foreclosed(client, now)
        base_o = "/api/v1/states/ogun/mortgages"
        for url, body in (
            (f"{base_o}/{mid}/possession", {"reference": "r"}),
            (f"{base_o}/{mid}/authorize-sale",
             {"valuation_kobo": 1, "reserve_price_kobo": 1, "valuer_id": "v"}),
            (f"{base_o}/{mid}/sell",
             {"purchaser_id": "b", "gross_proceeds_kobo": 1}),
            (f"{base_o}/{mid}/distribute-proceeds", None),
        ):
            r = client.post(url, json=body, headers=OTHER)
            assert r.status_code == 404, url
