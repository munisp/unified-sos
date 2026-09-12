"""Tests for mod-mortgage (mirrors mod-safecity-vision test style)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from mortgage_app.adapters import (
    AdapterUnavailableError,
    FixtureCreditScorer,
    FixtureLedgerAdapter,
    FixtureLandRegistry,
    HttpCreditScorer,
    HttpLandRegistry,
    TigerBeetleLedgerAdapter,
    credit_scorer_from_env,
    deterministic_transfer_id,
    land_registry_from_env,
    ledger_from_env,
    require_production_config,
)
from mortgage_app.domain import (
    MortgageStatus,
    MortgageStore,
    annuity_schedule,
)
from mortgage_app.main import create_app
from mortgage_app import events as ev

from conftest import (
    BASE,
    HEADERS,
    TENANT,
    FixedScorer,
    apply_payload,
    make_now,
    run_to_approved,
    run_to_disbursed,
    run_to_lien,
)

OTHER = {"X-State-Tenant": "ogun"}


# --- adapters ---------------------------------------------------------------
class TestAdapters:
    def test_fixture_scorer_deterministic_and_bounded(self):
        s = FixtureCreditScorer()
        for i in range(50):
            a = s.score(f"applicant-{i}")
            assert 300 <= a <= 850
            assert a == s.score(f"applicant-{i}")

    def test_http_scorer_fail_closed_without_url(self):
        with pytest.raises(AdapterUnavailableError):
            HttpCreditScorer(environ={})

    def test_http_scorer_unknown_engine_fail_closed(self):
        with pytest.raises(AdapterUnavailableError):
            credit_scorer_from_env({"SOS_MORTGAGE_CREDIT_ENGINE": "ouija"})

    def test_tigerbeetle_fail_closed_without_url(self):
        with pytest.raises(AdapterUnavailableError):
            TigerBeetleLedgerAdapter(environ={})

    def test_ledger_unknown_engine_fail_closed(self):
        with pytest.raises(AdapterUnavailableError):
            ledger_from_env({"SOS_MORTGAGE_LEDGER": "excel"})

    def test_lands_http_fail_closed_without_url(self):
        with pytest.raises(AdapterUnavailableError):
            HttpLandRegistry(environ={})

    def test_lands_unknown_engine_fail_closed(self):
        with pytest.raises(AdapterUnavailableError):
            land_registry_from_env({"SOS_MORTGAGE_LANDS": "pigeon"})

    def test_fixture_lands_title_ref_format(self):
        lands = FixtureLandRegistry()
        assert lands.parcel_exists("lagos", "p1", "LAG-2024-000123")
        assert not lands.parcel_exists("lagos", "p1", "not-a-title")

    def test_deterministic_transfer_id_128bit_stable(self):
        a = deterministic_transfer_id("k", "leg")
        assert a == deterministic_transfer_id("k", "leg")
        assert a != deterministic_transfer_id("k", "other")
        assert 0 <= a < 2**128

    def test_fixture_ledger_two_phase_semantics(self):
        ledger = FixtureLedgerAdapter()
        tid = deterministic_transfer_id("k1", "leg")
        ledger.hold(tid, 1, 2, 500, "k1")
        ledger.post(tid)
        assert ledger.balance(2) == ledger._opening + 500
        with pytest.raises(Exception):
            ledger.void(tid)  # posted can never be voided
        tid2 = deterministic_transfer_id("k2", "leg")
        ledger.hold(tid2, 1, 2, 100, "k2")
        ledger.void(tid2)
        with pytest.raises(Exception):
            ledger.post(tid2)  # voided can never be posted


# --- annuity schedule math ----------------------------------------------------
class TestAnnuity:
    @pytest.mark.parametrize("principal,rate_bps,term", [
        (600_000, 1200, 6), (1_000_000, 0, 12), (123_457, 999, 360),
        (50_000, 2500, 24),
    ])
    def test_conservation_and_full_amortization(self, principal, rate_bps, term):
        sched = annuity_schedule(principal, rate_bps, term, make_now())
        assert len(sched) == term
        total = sum(i.amount_kobo for i in sched)
        interest = sum(i.interest_kobo for i in sched)
        princ = sum(i.principal_kobo for i in sched)
        assert princ == principal
        assert total == principal + interest  # exact conservation, kobo
        for i in sched:
            assert i.amount_kobo == i.interest_kobo + i.principal_kobo

    def test_equal_installments_remainder_on_last(self):
        sched = annuity_schedule(600_000, 1200, 6, make_now())
        body = [i.amount_kobo for i in sched[:-1]]
        assert len(set(body)) == 1  # level payment
        assert sched[-1].amount_kobo != 0

    def test_zero_rate_equal_principal(self):
        sched = annuity_schedule(600_000, 0, 6, make_now())
        assert all(i.interest_kobo == 0 for i in sched)
        assert sum(i.amount_kobo for i in sched) == 600_000

    def test_term_bounds(self):
        with pytest.raises(ValueError):
            annuity_schedule(1000, 100, 5, make_now())
        with pytest.raises(ValueError):
            annuity_schedule(1000, 100, 361, make_now())


# --- API: application & credit review ------------------------------------------
class TestApplicationAndCredit:
    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}

    def test_missing_tenant_header_400(self, client):
        r = client.post(BASE + "/", json=apply_payload())
        assert r.status_code == 400

    def test_tenant_mismatch_404(self, client):
        r = client.post(BASE + "/", json=apply_payload(), headers=OTHER)
        assert r.status_code == 404

    def test_apply_returns_application_status(self, client, bus):
        r = client.post(BASE + "/", json=apply_payload(), headers=HEADERS)
        assert r.status_code == 201
        body = r.json()
        assert body["mortgage"]["status"] == "APPLICATION"
        assert body["chain_valid"] is True
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_APPLICATION_RECEIVED in topics

    def test_credit_review_approves_high_score(self, client):
        mid = run_to_approved(client)
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        assert body["mortgage"]["status"] == "APPROVED"
        assert body["mortgage"]["credit_score"] == 700

    def test_credit_review_declines_low_score(self, bus, make_store):
        client = TestClient(create_app(store=make_store(score=400), bus=bus))
        r = client.post(BASE + "/", json=apply_payload(), headers=HEADERS)
        mid = r.json()["mortgage"]["mortgage_id"]
        r = client.post(f"{BASE}/{mid}/credit-review", headers=HEADERS)
        assert r.json()["mortgage"]["status"] == "DECLINED"
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_DECLINED in topics

    def test_credit_review_manual_review_band(self, bus, make_store):
        client = TestClient(create_app(store=make_store(score=575), bus=bus))
        r = client.post(BASE + "/", json=apply_payload(), headers=HEADERS)
        mid = r.json()["mortgage"]["mortgage_id"]
        r = client.post(f"{BASE}/{mid}/credit-review", headers=HEADERS)
        assert r.json()["mortgage"]["status"] == "MANUAL_REVIEW"

    def test_manual_approve_requires_officer_and_reason(self, bus, make_store):
        client = TestClient(create_app(store=make_store(score=575), bus=bus))
        mid = client.post(BASE + "/", json=apply_payload(), headers=HEADERS
                          ).json()["mortgage"]["mortgage_id"]
        client.post(f"{BASE}/{mid}/credit-review", headers=HEADERS)
        r = client.post(f"{BASE}/{mid}/approve",
                        json={"officer": "", "reason": ""}, headers=HEADERS)
        assert r.status_code == 422
        r = client.post(f"{BASE}/{mid}/approve",
                        json={"officer": "off-9", "reason": "verified income"},
                        headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["mortgage"]["status"] == "APPROVED"
        assert r.json()["mortgage"]["approved_by"] == "off-9"

    def test_manual_approve_wrong_state_409(self, client):
        mid = run_to_approved(client)  # already APPROVED (score 700)
        r = client.post(f"{BASE}/{mid}/approve",
                        json={"officer": "o", "reason": "r"}, headers=HEADERS)
        assert r.status_code == 409

    def test_credit_review_twice_409(self, client):
        mid = run_to_approved(client)
        r = client.post(f"{BASE}/{mid}/credit-review", headers=HEADERS)
        assert r.status_code == 409


# --- liens ---------------------------------------------------------------------
class TestLiens:
    def test_register_lien_requires_approved(self, client):
        r = client.post(BASE + "/", json=apply_payload(), headers=HEADERS)
        mid = r.json()["mortgage"]["mortgage_id"]
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409

    def test_register_lien_happy(self, client):
        mid = run_to_lien(client)
        liens = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                           headers=HEADERS).json()
        assert len(liens) == 1
        assert liens[0]["priority"] == 1
        assert liens[0]["mortgage_id"] == mid

    def test_double_lien_conflict(self, client):
        run_to_lien(client)  # first lien on parcel-001
        # second application on the SAME parcel
        mid2 = run_to_approved(client, applicant_id="applicant-002",
                               title_ref="LAG-2024-000124")
        r = client.post(f"{BASE}/{mid2}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 409

    def test_second_charge_priority(self, client):
        mid1 = run_to_lien(client)
        senior = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                            headers=HEADERS).json()[0]
        mid2 = run_to_approved(client, applicant_id="applicant-002",
                               title_ref="LAG-2024-000124")
        r = client.post(f"{BASE}/{mid2}/register-lien",
                        json={"second_charge": True,
                              "senior_lien_id": senior["lien_id"]},
                        headers=HEADERS)
        assert r.status_code == 201, r.text
        assert r.json()["priority"] == 2
        assert r.json()["senior_lien_id"] == senior["lien_id"]

    def test_second_charge_requires_valid_senior(self, client):
        run_to_lien(client)
        mid2 = run_to_approved(client, applicant_id="applicant-002",
                               title_ref="LAG-2024-000124")
        r = client.post(f"{BASE}/{mid2}/register-lien",
                        json={"second_charge": True, "senior_lien_id": "lien-bogus"},
                        headers=HEADERS)
        assert r.status_code == 409

    def test_max_two_liens(self, client):
        run_to_lien(client)
        liens = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                           headers=HEADERS).json()
        mid2 = run_to_approved(client, applicant_id="a2", title_ref="LAG-2024-000124")
        client.post(f"{BASE}/{mid2}/register-lien",
                    json={"second_charge": True, "senior_lien_id": liens[0]["lien_id"]},
                    headers=HEADERS)
        mid3 = run_to_approved(client, applicant_id="a3", title_ref="LAG-2024-000125")
        r = client.post(f"{BASE}/{mid3}/register-lien",
                        json={"second_charge": True, "senior_lien_id": liens[0]["lien_id"]},
                        headers=HEADERS)
        assert r.status_code == 409  # max 2 liens per parcel

    def test_unknown_title_ref_404(self, client):
        mid = run_to_approved(client, title_ref="bogus-title")
        r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
        assert r.status_code == 404


# --- disbursement -------------------------------------------------------------
class TestDisbursement:
    def test_disburse_before_lien_409(self, client):
        mid = run_to_approved(client)
        r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)
        assert r.status_code == 409

    def test_disburse_happy_two_phase(self, client, ledger):
        mid = run_to_disbursed(client)
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        m = body["mortgage"]
        assert m["status"] == "DISBURSED"
        tid = int(m["disbursement_transfer_id"])
        assert ledger.transfers[tid]["state"] == "POSTED"
        # conservation: borrower received exactly the approved principal
        store = client.app.state.store
        borrower = store.applicant_account("applicant-001")
        assert ledger.balance(borrower) == ledger._opening + 600_000
        assert len(m["schedule"]) == 6

    def test_disburse_idempotent_replay(self, client):
        mid = run_to_disbursed(client)
        r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["mortgage"]["status"] == "DISBURSED"

    def test_void_on_post_failure(self, bus, make_store, ledger):
        client = TestClient(create_app(store=make_store(), bus=bus), raise_server_exceptions=False)
        mid = run_to_lien(client)
        ledger.fail_on_post = True
        r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)
        assert r.status_code == 500  # ledger error surfaces; nothing half-applied
        mid_body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert mid_body["status"] == "LIEN_REGISTERED"
        assert all(t["state"] == "VOIDED" for t in ledger.transfers.values())


# --- payments -------------------------------------------------------------------
class TestPayments:
    def test_payment_oldest_first_interest_then_principal(self, client):
        mid = run_to_disbursed(client)
        sched = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()
        first = sched["installments"][0]
        # pay exactly the first installment's interest
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": first["interest_kobo"],
                              "idempotency_key": "p1"}, headers=HEADERS)
        assert r.status_code == 201, r.text
        p = r.json()
        assert p["interest_kobo"] == first["interest_kobo"]
        assert p["principal_kobo"] == 0
        sched2 = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()
        assert sched2["installments"][0]["paid_interest_kobo"] == first["interest_kobo"]
        assert sched2["installments"][1]["paid_interest_kobo"] == 0

    def test_payment_idempotent_replay(self, client):
        mid = run_to_disbursed(client)
        body = {"amount_kobo": 10_000, "idempotency_key": "pay-1"}
        r1 = client.post(f"{BASE}/{mid}/payments", json=body, headers=HEADERS)
        r2 = client.post(f"{BASE}/{mid}/payments", json=body, headers=HEADERS)
        assert r1.status_code == 201 and r2.status_code == 201
        assert r1.json()["payment_id"] == r2.json()["payment_id"]
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert len(m["payments"]) == 1

    def test_payment_idempotency_conflict_409(self, client):
        mid = run_to_disbursed(client)
        client.post(f"{BASE}/{mid}/payments",
                    json={"amount_kobo": 10_000, "idempotency_key": "pay-1"},
                    headers=HEADERS)
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": 9_999, "idempotency_key": "pay-1"},
                        headers=HEADERS)
        assert r.status_code == 409

    def test_payment_before_disburse_409(self, client):
        mid = run_to_lien(client)
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": 1, "idempotency_key": "x"},
                        headers=HEADERS)
        assert r.status_code == 409

    def test_overpayment_reduces_principal(self, client):
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        extra = 50_000
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": total + extra, "idempotency_key": "big"},
                        headers=HEADERS)
        assert r.status_code == 201
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["outstanding_principal_kobo"] == 0

    def test_full_repayment_discharges_and_releases_lien(self, client, bus):
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        client.post(f"{BASE}/{mid}/payments",
                    json={"amount_kobo": total, "idempotency_key": "payoff"},
                    headers=HEADERS)
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        assert body["mortgage"]["status"] == "DISCHARGED"
        lien = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                          headers=HEADERS).json()[0]
        assert lien["status"] == "RELEASED"
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_DISCHARGED in topics
        assert ev.EVENT_PAYMENT_APPLIED in topics
        assert ev.EVENT_DISBURSED in topics

    def test_no_discharge_when_ledger_post_fails(self, bus, make_store, ledger):
        client = TestClient(create_app(store=make_store(), bus=bus), raise_server_exceptions=False)
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        ledger.fail_on_post = True
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": total, "idempotency_key": "payoff"},
                        headers=HEADERS)
        assert r.status_code == 500
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["status"] == "DISBURSED"  # no discharge without posted funds
        lien = client.get(BASE + "/liens", params={"parcel_id": "parcel-001"},
                          headers=HEADERS).json()[0]
        assert lien["status"] == "REGISTERED"

    def test_zero_rate_full_path_to_discharge(self, bus, make_store):
        client = TestClient(create_app(store=make_store(), bus=bus))
        mid = run_to_disbursed(client, rate_bps=0)
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": 600_000, "idempotency_key": "all"},
                        headers=HEADERS)
        assert r.status_code == 201
        assert client.get(f"{BASE}/{mid}", headers=HEADERS
                          ).json()["mortgage"]["status"] == "DISCHARGED"


# --- default & foreclosure --------------------------------------------------------
class TestDefaultForeclosure:
    def _store_with_clock(self, make_store):
        now = [make_now()]
        store = make_store(clock=lambda: now[0])
        return store, now

    def test_default_after_90_days(self, bus, make_store):
        store, now = self._store_with_clock(make_store)
        client = TestClient(create_app(store=store, bus=bus))
        mid = run_to_disbursed(client)
        now[0] = now[0] + timedelta(days=200)  # first due +1mo, >90d past due
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        assert body["mortgage"]["status"] == "DEFAULTED"
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_DEFAULTED in topics

    def test_not_default_within_90_days(self, bus, make_store):
        store, now = self._store_with_clock(make_store)
        client = TestClient(create_app(store=store, bus=bus))
        mid = run_to_disbursed(client)
        now[0] = now[0] + timedelta(days=30 + 60)
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        assert body["mortgage"]["status"] == "DISBURSED"

    def test_foreclose_records_reason(self, bus, make_store):
        store, now = self._store_with_clock(make_store)
        client = TestClient(create_app(store=store, bus=bus))
        mid = run_to_disbursed(client)
        r = client.post(f"{BASE}/{mid}/foreclose", json={"reason": "early"},
                        headers=HEADERS)
        assert r.status_code == 409  # not defaulted yet
        now[0] = now[0] + timedelta(days=200)
        r = client.post(f"{BASE}/{mid}/foreclose",
                        json={"reason": "90+ days arrears"}, headers=HEADERS)
        assert r.status_code == 200
        m = r.json()["mortgage"]
        assert m["status"] == "FORECLOSED"
        assert m["foreclosure_reason"] == "90+ days arrears"
        topics = [e["topic"] for e in bus.published]
        assert ev.EVENT_FORECLOSED in topics


# --- tenancy ---------------------------------------------------------------------
class TestTenancy:
    def test_cross_tenant_read_404(self, client):
        mid = run_to_approved(client)
        r = client.get(f"/api/v1/states/ogun/mortgages/{mid}", headers=OTHER)
        assert r.status_code == 404

    def test_cross_tenant_list_isolated(self, client):
        run_to_approved(client)
        r = client.get("/api/v1/states/ogun/mortgages/", headers=OTHER)
        assert r.status_code == 200
        assert r.json() == []

    def test_list_filters(self, client):
        run_to_approved(client, applicant_id="a1", parcel_id="parcel-001")
        run_to_approved(client, applicant_id="a2", parcel_id="parcel-002")
        r = client.get(BASE + "/", params={"applicant_id": "a1"}, headers=HEADERS)
        assert len(r.json()) == 1
        r = client.get(BASE + "/", params={"status_filter": "APPROVED"},
                       headers=HEADERS)
        assert len(r.json()) == 2


# --- audit chain ------------------------------------------------------------------
class TestAudit:
    def test_hash_chain_intact(self, client):
        mid = run_to_disbursed(client)
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        assert body["chain_valid"] is True
        kinds = [e["kind"] for e in body["audit"]]
        assert kinds == ["application_received", "transition", "credit_scored",
                         "lien_registered", "disbursed"]

    def test_tamper_detection(self, bus, make_store):
        store = make_store()
        client = TestClient(create_app(store=store, bus=bus))
        mid = run_to_disbursed(client)
        feed = store.audit_feed(TENANT, mid)
        feed[1]["kind"] = "tampered"  # mutate in place
        errors = store.verify_audit_chain(TENANT, mid)
        assert errors  # chain broken
        body = client.get(f"{BASE}/{mid}", headers=HEADERS).json()
        assert body["chain_valid"] is False


# --- metrics & production profile ---------------------------------------------------
class TestOps:
    def test_metrics_counters(self, client):
        mid = run_to_disbursed(client)
        client.post(f"{BASE}/{mid}/payments",
                    json={"amount_kobo": 1000, "idempotency_key": "m1"},
                    headers=HEADERS)
        text = client.get("/metrics").text
        assert "mortgage_applications_total" in text
        assert "mortgage_approvals_total" in text
        assert "mortgage_disbursements_total" in text
        assert "mortgage_kobo_disbursed_total" in text
        assert "mortgage_payments_total" in text

    def test_production_boot_fail_closed(self, monkeypatch):
        monkeypatch.setenv("SOS_MORTGAGE_PROFILE", "production")
        monkeypatch.delenv("SOS_MORTGAGE_TB_URL", raising=False)
        monkeypatch.delenv("SOS_MORTGAGE_LANDS_URL", raising=False)
        with pytest.raises(AdapterUnavailableError):
            create_app()

    def test_production_boot_ok_when_configured(self, monkeypatch, make_store, bus):
        monkeypatch.setenv("SOS_MORTGAGE_PROFILE", "production")
        monkeypatch.setenv("SOS_MORTGAGE_TB_URL", "tb://cluster:3000")
        monkeypatch.setenv("SOS_MORTGAGE_LANDS_URL", "http://lands:8003")
        app = create_app(store=make_store(), bus=bus)
        assert app is not None

    def test_require_production_config_dev_noop(self, monkeypatch):
        monkeypatch.delenv("SOS_MORTGAGE_PROFILE", raising=False)
        require_production_config()  # no raise
