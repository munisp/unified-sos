"""Hardening tests: webhook conflict 409, integer split legs, ledger chain,
escrow ordering/expiry, hash-chain audit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters import AdapterUnavailableError, FixtureFspiopAdapter, FixtureNibssAdapter
from app.adapters.ledger import (
    FixtureSettlementLedger,
    TigerBeetleSettlementLedger,
    select_ledger_adapter,
)
from app.domain import (
    BillEventConflict,
    EscrowState,
    MobilityStore,
    SettlementRejectedError,
    request_hash,
)
from app.main import NibssBillNotification, create_app

FARE_TABLE = {
    "gazette_reference": "LAMATA-HARMONIZATION-2026-03",
    "union_commission_pct": 5.0,
    "fares": [{"mode": "bus", "route": "*", "fare_kobo": 50000}],
}


def _client(store=None, fspiop=None, nibss=None) -> TestClient:
    client = TestClient(create_app(store=store, fspiop=fspiop, nibss=nibss))
    assert client.put("/mobility/v1/fares/lagos", json=FARE_TABLE).status_code == 200
    return client


def _tap(client: TestClient, fare_route_ok: bool = True, operator: str = "lbsl",
         card: str = "cowry-00aa") -> None:
    resp = client.post("/mobility/v1/clearing", json={
        "tenant_state_id": "lagos", "operator_id": operator,
        "mode": "bus", "route": "BRT-1", "card_ref": card,
    })
    assert resp.status_code == 201, resp.text


def _notify(client: TestClient, nibss: FixtureNibssAdapter, ref: str,
            amount: int) -> "httpx.Response":
    payload = NibssBillNotification(bill_reference=ref, amount_kobo=amount)
    body = payload.model_dump_json().encode("utf-8")
    headers = {"X-NIBSS-Signature": nibss.sign_notification(body),
               "Content-Type": "application/json"}
    return client.post("/mobility/v1/webhooks/nibss/ebills", content=body,
                       headers=headers)


# --- A1: webhook request-hash conflict ---------------------------------------

def test_bill_event_replay_same_payload_ok() -> None:
    nibss = FixtureNibssAdapter()
    client = _client(nibss=nibss)
    ok = _notify(client, nibss, "BILL-1", 50000)
    assert ok.status_code == 200
    replay = _notify(client, nibss, "BILL-1", 50000)
    assert replay.status_code == 200
    assert replay.json()["event"] == ok.json()["event"]


def test_bill_event_conflict_different_amount_409() -> None:
    nibss = FixtureNibssAdapter()
    client = _client(nibss=nibss)
    assert _notify(client, nibss, "BILL-2", 50000).status_code == 200
    conflict = _notify(client, nibss, "BILL-2", 75000)
    assert conflict.status_code == 409


def test_bill_event_conflict_audit_logged_hash_chain() -> None:
    store = MobilityStore()
    client = _client(store=store, nibss=FixtureNibssAdapter())
    nibss = client.app.state.nibss
    _notify(client, nibss, "BILL-3", 50000)
    _notify(client, nibss, "BILL-3", 99000)
    audit = client.get("/mobility/v1/audit").json()
    assert audit["chain_errors"] == []
    types = [e["event_type"] for e in audit["events"]]
    assert "bill_event_recorded" in types and "bill_event_conflict" in types
    conflict = next(e for e in audit["events"]
                    if e["event_type"] == "bill_event_conflict")
    assert conflict["bill_reference"] == "BILL-3"
    assert conflict["existing_request_hash"] != conflict["rejected_request_hash"]


def test_record_bill_event_store_level_conflict() -> None:
    store = MobilityStore()
    event = {"bill_reference": "B", "amount_kobo": 100, "received_at": "t0"}
    store.record_bill_event("B", dict(event))
    assert store.record_bill_event("B", {**event, "received_at": "t1"})["amount_kobo"] == 100
    with pytest.raises(BillEventConflict):
        store.record_bill_event("B", {**event, "amount_kobo": 200})
    assert store.verify_audit_chain() == []


def test_request_hash_order_independent() -> None:
    assert request_hash({"a": 1, "b": 2}) == request_hash({"b": 2, "a": 1})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


# --- A2: integer legs + zero-leg rejection + ledger chain ---------------------

def test_settlement_legs_integer_exactness() -> None:
    # gross 100_001 at 5% union: floor(100001*500/10000)=5000,
    # floor(100001*1000/10000)=10000, remainder 85001 → sums exactly.
    client = _client()
    client.put("/mobility/v1/fares/lagos", json={
        **FARE_TABLE, "fares": [{"mode": "bus", "route": "*", "fare_kobo": 33333}],
    })
    for card in ("cowry-0000", "cowry-0002", "cowry-0004"):
        _tap(client, card=card)
    # plus 2 kobo to make gross 100_001
    client.put("/mobility/v1/fares/lagos", json={
        **FARE_TABLE, "fares": [{"mode": "bus", "route": "*", "fare_kobo": 2}],
    })
    _tap(client, card="cowry-0006")
    batch = client.post("/mobility/v1/settlements/lagos/lbsl").json()
    assert batch["gross_kobo"] == 100001
    legs = {l["beneficiary"]: l["amount_kobo"] for l in batch["legs"]}
    assert legs["TRANSPORT_UNION_COMMISSION"] == 5000
    assert legs["STATE_CONSOLIDATED_REVENUE_FUND"] == 10000
    assert legs["OPERATOR_RETENTION"] == 85001
    assert sum(legs.values()) == 100001
    assert all(isinstance(v, int) for v in legs.values())


def test_settlement_zero_leg_rejected_nothing_settled() -> None:
    store = MobilityStore()
    client = _client(store=store)
    # Tiny fare → union leg floors to 0 kobo.
    client.put("/mobility/v1/fares/lagos", json={
        **FARE_TABLE, "fares": [{"mode": "bus", "route": "*", "fare_kobo": 10}],
    })
    _tap(client)
    resp = client.post("/mobility/v1/settlements/lagos/lbsl")
    assert resp.status_code == 409
    assert "zero-value legs" in resp.json()["detail"]
    # Records were NOT marked settled; no batch recorded; ledger untouched.
    assert client.get("/mobility/v1/settlements").json() == []
    rec = next(iter(store.clearing.values()))
    assert rec.settled_batch_id is None
    assert store.ledger.posts == {}


def test_settlement_executes_linked_hold_post_chain() -> None:
    store = MobilityStore()
    client = _client(store=store)
    _tap(client, card="cowry-00aa")
    _tap(client, card="cowry-00ac")
    batch = client.post("/mobility/v1/settlements/lagos/lbsl").json()
    posts = list(store.ledger.posts.values())
    assert len(posts) == 3  # one posting per leg
    assert all(p["batch_id"] == batch["batch_id"] for p in posts)
    # Linked chain: first leg unlinked, each later hold links the previous.
    assert posts[0]["linked_id"] is None
    link_ids = [p["linked_id"] for p in posts[1:]]
    assert set(link_ids) == {p["hold_id"] for p in posts[:2]}
    assert store.verify_audit_chain() == []
    types = [e["event_type"] for e in store.audit_chain]
    assert "settlement_executed" in types


def test_settlement_ledger_failure_leaves_records_unsettled() -> None:
    class FailingLedger(FixtureSettlementLedger):
        def execute_linked_chain(self, batch_id, legs):
            raise AdapterUnavailableError("ledger down")

    store = MobilityStore(ledger=FailingLedger())
    client = _client(store=store)
    _tap(client)
    resp = client.post("/mobility/v1/settlements/lagos/lbsl")
    assert resp.status_code == 503
    rec = next(iter(store.clearing.values()))
    assert rec.settled_batch_id is None


def test_zero_leg_rejected_store_level() -> None:
    from app.domain import FareRule, FareTable, Mode, _now

    store = MobilityStore()
    store.set_fare_table(FareTable(
        tenant_state_id="lagos", gazette_reference="g",
        union_commission_pct=3.0,
        fares=[FareRule(mode=Mode.BUS, route="*", fare_kobo=5)],
        updated_at=_now()))
    store.record_tap("lagos", "op", Mode.BUS, "R1", "card-0")
    with pytest.raises(SettlementRejectedError):
        store.settle_operator("lagos", "op")


def test_ledger_adapter_fail_closed_production(monkeypatch) -> None:
    monkeypatch.delenv("SOS_MOBILITY_LEDGER_URL", raising=False)
    monkeypatch.setenv("SOS_PROFILE", "production")
    with pytest.raises(AdapterUnavailableError):
        select_ledger_adapter()
    monkeypatch.setenv("SOS_PROFILE", "dev")
    assert isinstance(select_ledger_adapter(), FixtureSettlementLedger)
    with pytest.raises(AdapterUnavailableError):
        TigerBeetleSettlementLedger(url=None)


# --- A3: escrow ordering / expiry ----------------------------------------------

def _settled_batch(client: TestClient) -> dict:
    _tap(client)
    return client.post("/mobility/v1/settlements/lagos/lbsl").json()


def test_escrow_local_record_then_scheme_prepare() -> None:
    fspiop = FixtureFspiopAdapter()
    client = _client(fspiop=fspiop)
    batch = _settled_batch(client)
    resp = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-1", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"], "condition": "",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["state"] == "pending"
    assert resp.json()["expires_at"] > resp.json()["created_at"]
    assert "tx-1" in fspiop._prepared  # scheme-side prepare happened


def test_escrow_scheme_failure_compensating_abort() -> None:
    class DownFspiop(FixtureFspiopAdapter):
        def transfer_prepare(self, *a, **k):
            raise AdapterUnavailableError("scheme unreachable")

    store = MobilityStore()
    client = _client(store=store, fspiop=DownFspiop())
    batch = _settled_batch(client)
    resp = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-2", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    assert resp.status_code == 503
    # Compensated: local record is VOID, no pending orphan.
    assert store.escrows["tx-2"].state == EscrowState.VOID
    assert "tx-2" not in client.app.state.fspiop._prepared


def test_escrow_expiry_sweep_auto_aborts() -> None:
    now = [1_700_000_000.0]
    store = MobilityStore(clock=lambda: now[0])
    client = _client(store=store, fspiop=FixtureFspiopAdapter())
    batch = _settled_batch(client)
    resp = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-3", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    assert resp.status_code == 201
    now[0] += 16 * 60  # past the 15-minute TTL
    sweep = client.post("/mobility/v1/escrow/sweep")
    assert sweep.status_code == 200
    voided = sweep.json()
    assert [e["transfer_id"] for e in voided] == ["tx-3"]
    assert voided[0]["state"] == "void"
    # Sweep is idempotent.
    assert client.post("/mobility/v1/escrow/sweep").json() == []


def test_expired_escrow_fulfil_rejected_409_and_aborted() -> None:
    now = [1_700_000_000.0]
    store = MobilityStore(clock=lambda: now[0])
    client = _client(store=store, fspiop=FixtureFspiopAdapter())
    batch = _settled_batch(client)
    client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-4", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    now[0] += 16 * 60
    resp = client.post("/mobility/v1/escrow/tx-4/fulfil")
    assert resp.status_code == 409
    assert store.escrows["tx-4"].state == EscrowState.VOID


def test_escrow_ttl_injectable() -> None:
    store = MobilityStore(clock=lambda: 1_700_000_000.0, escrow_ttl_seconds=60)
    client = _client(store=store)
    batch = _settled_batch(client)
    resp = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-5", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    rec = resp.json()
    from datetime import datetime
    delta = (datetime.fromisoformat(rec["expires_at"])
             - datetime.fromisoformat(rec["created_at"])).total_seconds()
    assert delta == 60
