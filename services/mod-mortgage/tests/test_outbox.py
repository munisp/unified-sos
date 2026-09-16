"""Tests: transactional outbox (atomic domain+event commit, relay retry,
no direct publish) and overpayment/credit handling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _shared.eventbus import InMemoryEventBus
from mortgage_app import events as ev
from mortgage_app.adapters import AdapterUnavailableError
from mortgage_app.main import create_app
from mortgage_app.outbox import (
    InMemoryOutbox,
    OutboxRelay,
    PostgresOutbox,
    outbox_from_env,
)

from conftest import (
    BASE,
    HEADERS,
    apply_payload,
    run_to_approved,
    run_to_disbursed,
    run_to_lien,
)


class FlakyBus(InMemoryEventBus):
    """In-memory bus with a crash-injection hook on publish."""

    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def publish(self, topic, payload) -> None:
        if self.fail:
            raise RuntimeError("injected bus publish failure")
        super().publish(topic, payload)


@pytest.fixture()
def flaky_bus() -> FlakyBus:
    return FlakyBus()


# --- outbox seams -----------------------------------------------------------------
class TestOutboxSeams:
    def test_postgres_outbox_fail_closed_without_dsn(self):
        with pytest.raises(AdapterUnavailableError):
            PostgresOutbox(environ={})

    def test_outbox_unknown_kind_fail_closed(self):
        with pytest.raises(AdapterUnavailableError):
            outbox_from_env({"SOS_MORTGAGE_OUTBOX": "carrier-pigeon"})

    def test_outbox_from_env_default_fixture(self):
        assert isinstance(outbox_from_env({}), InMemoryOutbox)

    def test_relay_acks_only_on_success(self):
        outbox = InMemoryOutbox()
        bus = FlakyBus()
        relay = OutboxRelay(outbox, bus=type("B", (), {"publish": lambda s, t, p: bus.publish(t, type("E", (), {"model_dump_json": lambda s2: "{}"})())})())
        row = outbox.append("t", {"a": 1}, "k1")
        bus.fail = True
        assert relay.publish_pending() == 0
        assert outbox.unacked()  # still pending
        bus.fail = False
        assert relay.publish_pending() == 1
        assert not outbox.unacked()
        assert row.attempts == 2


# --- atomicity & relay over the API -------------------------------------------------
class TestOutboxAtomicity:
    def test_disburse_commits_event_row_with_money_move(self, client, ledger):
        store = client.app.state.store
        mid = run_to_lien(client)
        rows_before = len(store.outbox.rows)
        r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)
        assert r.status_code == 200
        # money moved AND the event row exists and was delivered by the drain
        tid = int(r.json()["mortgage"]["disbursement_transfer_id"])
        assert ledger.transfers[tid]["state"] == "POSTED"
        new_rows = store.outbox.rows[rows_before:]
        assert any(row.topic == ev.EVENT_DISBURSED and row.published
                   for row in new_rows)

    def test_publish_failure_then_relay_retry_delivers(self, flaky_bus, make_store):
        client = TestClient(create_app(store=make_store(), bus=flaky_bus))
        mid = run_to_lien(client)
        n_bus = len(flaky_bus.published)
        flaky_bus.fail = True
        r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)
        assert r.status_code == 200  # domain commit unaffected by bus failure
        assert len(flaky_bus.published) == n_bus  # nothing delivered
        store = client.app.state.store
        assert any(not row.published for row in store.outbox.rows)  # un-acked row
        flaky_bus.fail = False
        r = client.post("/internal/outbox/relay")
        assert r.status_code == 200 and r.json()["published"] >= 1
        topics = [e["topic"] for e in flaky_bus.published]
        assert ev.EVENT_DISBURSED in topics
        assert not store.outbox.unacked()  # all acked after retry

    def test_relay_second_pass_is_noop(self, client):
        run_to_disbursed(client)
        assert client.post("/internal/outbox/relay").json()["published"] == 0

    def test_no_direct_publish_bypasses_outbox(self, flaky_bus, make_store):
        # every lifecycle event on the bus came through an outbox row
        client = TestClient(create_app(store=make_store(), bus=flaky_bus))
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        client.post(f"{BASE}/{mid}/payments",
                    json={"amount_kobo": total, "idempotency_key": "payoff"},
                    headers=HEADERS)
        store = client.app.state.store
        bus_topics = [e["topic"] for e in flaky_bus.published]
        outbox_topics = [r.topic for r in store.outbox.rows]
        assert sorted(bus_topics) == sorted(outbox_topics)  # 1:1, no bypass
        assert ev.EVENT_DISCHARGED in bus_topics

    def test_disburse_replay_exactly_once_domain_effect(self, client):
        store = client.app.state.store
        mid = run_to_disbursed(client)
        n_rows = len(store.outbox.rows)
        r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)  # replay
        assert r.status_code == 200
        disbursed_rows = [r for r in store.outbox.rows
                          if r.topic == ev.EVENT_DISBURSED]
        assert len(disbursed_rows) == 1  # no duplicate event
        assert len(store.outbox.rows) == n_rows

    def test_event_envelope_carries_idempotency_key(self, client):
        mid = run_to_approved(client)
        app_evt = [e for e in client.app.state.bus.published
                   if e["topic"] == ev.EVENT_APPLICATION_RECEIVED][0]
        payload = app_evt["payload"]["payload"]
        assert payload["idempotency_key"] == f"{mid}|application_received"
        assert payload["event_id"]


# --- overpayment / borrower credit ---------------------------------------------------
class TestOverpayment:
    def test_overpayment_rejected_400_by_default(self, client):
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": total + 1, "idempotency_key": "over"},
                        headers=HEADERS)
        assert r.status_code == 400
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["payments"] == []  # nothing applied

    def test_allow_credit_routes_excess(self, client):
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": total + 25_000,
                              "idempotency_key": "over", "allow_credit": True},
                        headers=HEADERS)
        assert r.status_code == 201
        p = r.json()
        assert p["overpayment_kobo"] == 25_000
        assert p["interest_kobo"] + p["principal_kobo"] + p["overpayment_kobo"] \
            == p["amount_kobo"]

    def test_discharge_refunds_credit_balance(self, client, ledger):
        store = client.app.state.store
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        client.post(f"{BASE}/{mid}/payments",
                    json={"amount_kobo": total + 25_000, "idempotency_key": "over",
                          "allow_credit": True},
                    headers=HEADERS)
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["status"] == "DISCHARGED"
        assert m["credit_balance_kobo"] == 0  # refunded, not stranded
        refund_tid = int(m["credit_refund_transfer_id"])
        assert ledger.transfers[refund_tid]["state"] == "POSTED"
        assert ledger.transfers[refund_tid]["amount_kobo"] == 25_000
        credit_acct = store.borrower_credit_account("applicant-001")
        assert ledger.balance(credit_acct) == ledger._opening + 25_000
        kinds = [e["kind"] for e in store.audit_feed("lagos", mid)]
        assert "credit_refunded" in kinds

    def test_exact_outstanding_payment_no_credit(self, client):
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        r = client.post(f"{BASE}/{mid}/payments",
                        json={"amount_kobo": total, "idempotency_key": "exact",
                              "allow_credit": True},
                        headers=HEADERS)
        assert r.status_code == 201
        assert r.json()["overpayment_kobo"] == 0
        m = client.get(f"{BASE}/{mid}", headers=HEADERS).json()["mortgage"]
        assert m["credit_refund_transfer_id"] is None

    def test_payment_audit_records_overpayment(self, client):
        store = client.app.state.store
        mid = run_to_disbursed(client)
        total = client.get(f"{BASE}/{mid}/schedule", headers=HEADERS).json()["total_kobo"]
        client.post(f"{BASE}/{mid}/payments",
                    json={"amount_kobo": total + 7_777, "idempotency_key": "o2",
                          "allow_credit": True},
                    headers=HEADERS)
        pay_evt = [e for e in store.audit_feed("lagos", mid)
                   if e["kind"] == "payment_applied"][0]
        assert pay_evt["overpayment_kobo"] == 7_777
