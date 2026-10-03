"""Tests for the scheme adapters (Mojaloop FSPIOP + NIBSS e-Bills).

Covers: fail-closed default, fixture determinism, signature rejection,
idempotent replay, and the escrow settlement flow.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters import (
    AdapterUnavailableError,
    FspiopAdapter,
    NibssEBillsAdapter,
    SettlementRow,
)
from app.adapters.fixtures import (
    FIXTURE_SECRET,
    FixtureFspiopAdapter,
    FixtureNibssAdapter,
    fixture_fulfilment_for,
)
from app.main import create_app

FARE_TABLE = {
    "gazette_reference": "LAMATA-HARMONIZATION-2026-03",
    "union_commission_pct": 5.0,
    "fares": [{"mode": "bus", "route": "*", "fare_kobo": 50000}],
}


# --- fail-closed defaults -----------------------------------------------------

def test_fspiop_adapter_fails_closed_by_default() -> None:
    adapter = FspiopAdapter()
    with pytest.raises(AdapterUnavailableError):
        adapter.party_lookup("MSISDN", "08030000000")
    with pytest.raises(AdapterUnavailableError):
        adapter.quote("tx-1", 1000, "payer", "payee")
    with pytest.raises(AdapterUnavailableError):
        adapter.transfer_prepare("tx-1", 1000, "cond", "2026-01-01T00:00:00Z")
    with pytest.raises(AdapterUnavailableError):
        adapter.transfer_fulfil("tx-1", "fulfil")


def test_fspiop_enabled_without_peer_fails_closed() -> None:
    adapter = FspiopAdapter(enabled=True)  # no base_url / client
    with pytest.raises(AdapterUnavailableError):
        adapter.transfer_prepare("tx-1", 1000, "cond", "exp")


def test_nibss_adapter_fails_closed_by_default() -> None:
    adapter = NibssEBillsAdapter()
    with pytest.raises(AdapterUnavailableError):
        adapter.create_bill("BILL-1", 1000, "payer")
    with pytest.raises(AdapterUnavailableError):
        list(adapter.settlement_report("2026-01-01"))


def test_signature_verification_fails_closed_without_secret() -> None:
    adapter = FspiopAdapter(enabled=True, base_url="http://peer")  # no secret
    assert adapter.verify_inbound_signature(
        {"FSPIOP-Signature": "sha256=deadbeef"}, b"{}") is False
    nibss = NibssEBillsAdapter(enabled=True, base_url="http://peer")
    assert nibss.verify_notification_signature(
        {"X-NIBSS-Signature": "deadbeef"}, b"{}") is False


# --- fixture determinism ------------------------------------------------------

def test_fixture_fspiop_deterministic_and_idempotent() -> None:
    a, b = FixtureFspiopAdapter(), FixtureFspiopAdapter()
    q1 = a.quote("tx-det-1", 100000, "payer", "payee")
    q2 = b.quote("tx-det-1", 100000, "payer", "payee")
    assert q1 == q2  # deterministic across instances
    assert q1["payeeFspFeeMinor"] == str(100000 // 100 + 1000)

    p1 = a.transfer_prepare("tx-det-1", 100000, q1["condition"], "2030-01-01T00:00:00Z")
    p2 = a.transfer_prepare("tx-det-1", 100000, q1["condition"], "2030-01-01T00:00:00Z")
    assert p1 == p2 and p1["state"] == "PENDING"  # idempotent replay

    f1 = a.transfer_fulfil("tx-det-1", "")
    f2 = a.transfer_fulfil("tx-det-1", "")
    assert f1 == f2 and f1["transferState"] == "COMMITTED"
    assert fixture_fulfilment_for("tx-det-1").fulfilment == f1["fulfilment"]


def test_fixture_fulfil_of_unprepared_transfer_rejected() -> None:
    with pytest.raises(KeyError):
        FixtureFspiopAdapter().transfer_fulfil("tx-ghost", "x")


def test_fixture_nibss_deterministic() -> None:
    a = FixtureNibssAdapter()
    bill1 = a.create_bill("BILL-FIX-1", 250000, "payer-stin")
    bill2 = FixtureNibssAdapter().create_bill("BILL-FIX-1", 250000, "payer-stin")
    assert bill1 == bill2  # deterministic gateway ref keyed by bill_reference
    assert a.create_bill("BILL-FIX-1", 250000, "payer-stin") == bill1  # replay


def test_settlement_sheet_reconciliation_iterator() -> None:
    expected = [
        SettlementRow("BILL-A", 100, "NIP", "2026-01-01T23:59:00Z", "r1"),
        SettlementRow("BILL-B", 200, "NIP", "2026-01-01T23:59:00Z", "r2"),
    ]
    actual = [
        SettlementRow("BILL-A", 100, "NIP", "2026-01-01T23:59:00Z", "r1"),
        SettlementRow("BILL-B", 150, "NIP", "2026-01-01T23:59:00Z", "r2"),
        SettlementRow("BILL-C", 999, "NIP", "2026-01-01T23:59:00Z", "r3"),
    ]
    breaks = list(NibssEBillsAdapter.reconcile_settlement_sheet(expected, actual))
    reasons = {b.bill_reference: (b.reason, b.expected_kobo, b.actual_kobo)
               for b in breaks}
    assert reasons == {
        "BILL-B": ("AMOUNT_MISMATCH", 200, 150),
        "BILL-C": ("UNEXPECTED_IN_SHEET", 0, 999),
    }
    assert list(NibssEBillsAdapter.reconcile_settlement_sheet(expected, expected)) == []


# --- HTTP: escrow flow + webhooks ----------------------------------------------

def _fixture_client() -> tuple[TestClient, FixtureFspiopAdapter, FixtureNibssAdapter]:
    fspiop = FixtureFspiopAdapter()
    nibss = FixtureNibssAdapter()
    client = TestClient(create_app(fspiop=fspiop, nibss=nibss))
    assert client.put("/mobility/v1/fares/lagos", json=FARE_TABLE).status_code == 200
    return client, fspiop, nibss


def _batch(client: TestClient) -> dict:
    client.post("/mobility/v1/clearing", json={
        "tenant_state_id": "lagos", "operator_id": "lbsl",
        "mode": "bus", "route": "BRT-1", "card_ref": "cowry-00aa",
    })
    resp = client.post("/mobility/v1/settlements/lagos/lbsl")
    assert resp.status_code == 201
    return resp.json()


def test_escrow_pending_post_void_flow_and_idempotency() -> None:
    client, fspiop, _ = _fixture_client()
    batch = _batch(client)

    # Pending on the escrow account (prepare), idempotent on transfer_id.
    p1 = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-esc-1", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    assert p1.status_code == 201
    assert p1.json()["state"] == "pending"
    p2 = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-esc-1", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    assert p2.status_code == 201 and p2.json() == p1.json()  # replay

    # Conflicting replay of the same transfer_id is rejected.
    conflict = client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-esc-1", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"] + 1,
    })
    assert conflict.status_code == 409

    # Fulfil posts the escrow; replay is idempotent.
    f1 = client.post("/mobility/v1/escrow/tx-esc-1/fulfil")
    assert f1.status_code == 200 and f1.json()["state"] == "posted"
    assert f1.json()["fulfilment"] == fspiop.transfer_fulfil("tx-esc-1", "")["fulfilment"]
    assert client.post("/mobility/v1/escrow/tx-esc-1/fulfil").json() == f1.json()
    # Aborting a posted escrow is a conflict.
    assert client.post("/mobility/v1/escrow/tx-esc-1/abort").status_code == 409

    # A second escrow aborted: void, idempotent, then fulfil is a conflict.
    client.post("/mobility/v1/escrow", json={
        "transfer_id": "tx-esc-2", "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    a1 = client.post("/mobility/v1/escrow/tx-esc-2/abort")
    assert a1.json()["state"] == "void"
    assert client.post("/mobility/v1/escrow/tx-esc-2/abort").json() == a1.json()
    assert client.post("/mobility/v1/escrow/tx-esc-2/fulfil").status_code == 409

    assert client.post("/mobility/v1/escrow/tx-ghost/fulfil").status_code == 404


def test_nibss_webhook_signature_and_idempotency() -> None:
    client, _, nibss = _fixture_client()
    from app.main import NibssBillNotification

    payload = NibssBillNotification(bill_reference="BILL-FIX-9",
                                    amount_kobo=50000, channel="NIP",
                                    provider_reference="prv-1")
    body = payload.model_dump_json().encode("utf-8")
    headers = {"X-NIBSS-Signature": nibss.sign_notification(body),
               "Content-Type": "application/json"}

    ok = client.post("/mobility/v1/webhooks/nibss/ebills", content=body,
                     headers=headers)
    assert ok.status_code == 200
    assert ok.json()["event"]["bill_reference"] == "BILL-FIX-9"
    # Idempotent replay on bill_reference returns the same event.
    replay = client.post("/mobility/v1/webhooks/nibss/ebills", content=body,
                         headers=headers)
    assert replay.status_code == 200
    assert replay.json()["event"] == ok.json()["event"]

    # Bad signature is rejected.
    bad = client.post("/mobility/v1/webhooks/nibss/ebills", content=body,
                      headers={"X-NIBSS-Signature": "deadbeef",
                               "Content-Type": "application/json"})
    assert bad.status_code == 401


# --- HTTP: inbound FSPIOP fulfilment callbacks ---------------------------------

def _escrow(client: TestClient, batch: dict, transfer_id: str) -> None:
    resp = client.post("/mobility/v1/escrow", json={
        "transfer_id": transfer_id, "batch_id": batch["batch_id"],
        "amount_kobo": batch["gross_kobo"],
    })
    assert resp.status_code == 201


def _signed_callback(client: TestClient, transfer_id: str, payload: dict,
                     secret: str, signature: str | None = None):
    from app.adapters.fspiop import compute_hmac_signature

    body = json.dumps(payload).encode("utf-8")
    sig = signature if signature is not None else compute_hmac_signature(secret, body)
    return client.put(f"/mobility/v1/fsps/callbacks/transfers/{transfer_id}",
                      content=body,
                      headers={"FSPIOP-Signature": sig,
                               "Content-Type": "application/json"})


def _fulfilment_payload(transfer_id: str, fulfilment: str = "fulfil-abc") -> dict:
    return {
        "transferId": transfer_id,
        "transferState": "COMMITTED",
        "fulfilment": fulfilment,
        "completedTimestamp": "2026-01-01T00:00:00Z",
    }


def test_fspiop_callback_happy_path_posts_escrow() -> None:
    from app.adapters.fixtures import FIXTURE_SECRET

    client, fspiop, _ = _fixture_client()
    batch = _batch(client)
    _escrow(client, batch, "tx-cb-1")

    resp = _signed_callback(client, "tx-cb-1", _fulfilment_payload("tx-cb-1"),
                            FIXTURE_SECRET)
    assert resp.status_code == 200
    assert resp.json()["state"] == "posted"
    assert resp.json()["fulfilment"] == "fulfil-abc"

    # Audit chain carries the applied callback and still verifies.
    audit = client.get("/mobility/v1/audit").json()
    assert audit["chain_errors"] == []
    assert any(e["event_type"] == "scheme_callback_applied"
               and e["transfer_id"] == "tx-cb-1" for e in audit["events"])


def test_fspiop_callback_rejects_bad_and_missing_signature() -> None:
    from app.adapters.fixtures import FIXTURE_SECRET

    client, _, _ = _fixture_client()
    batch = _batch(client)
    _escrow(client, batch, "tx-cb-2")

    bad = _signed_callback(client, "tx-cb-2", _fulfilment_payload("tx-cb-2"),
                           FIXTURE_SECRET, signature="sha256=deadbeef")
    assert bad.status_code == 401
    body = json.dumps(_fulfilment_payload("tx-cb-2")).encode("utf-8")
    missing = client.put("/mobility/v1/fsps/callbacks/transfers/tx-cb-2",
                         content=body,
                         headers={"Content-Type": "application/json"})
    assert missing.status_code == 401
    # Escrow untouched.
    assert client.post("/mobility/v1/escrow/tx-cb-2/abort").json()["state"] == "void"


def test_fspiop_callback_fails_closed_without_adapter() -> None:
    client = TestClient(create_app())
    resp = client.put("/mobility/v1/fsps/callbacks/transfers/tx-x",
                      json=_fulfilment_payload("tx-x"))
    assert resp.status_code == 503


def test_fspiop_callback_idempotent_replay_and_conflict() -> None:
    from app.adapters.fixtures import FIXTURE_SECRET

    client, _, _ = _fixture_client()
    batch = _batch(client)
    _escrow(client, batch, "tx-cb-3")

    payload = _fulfilment_payload("tx-cb-3")
    first = _signed_callback(client, "tx-cb-3", payload, FIXTURE_SECRET)
    assert first.status_code == 200
    replay = _signed_callback(client, "tx-cb-3", payload, FIXTURE_SECRET)
    assert replay.status_code == 200
    assert replay.json() == first.json()  # idempotent replay

    conflict = _signed_callback(
        client, "tx-cb-3", _fulfilment_payload("tx-cb-3", "fulfil-DIFFERENT"),
        FIXTURE_SECRET)
    assert conflict.status_code == 409
    audit = client.get("/mobility/v1/audit").json()
    assert any(e["event_type"] == "scheme_callback_conflict"
               and e["transfer_id"] == "tx-cb-3" for e in audit["events"])


def test_fspiop_callback_abort_voids_escrow_and_validates_payload() -> None:
    from app.adapters.fixtures import FIXTURE_SECRET

    client, _, _ = _fixture_client()
    batch = _batch(client)
    _escrow(client, batch, "tx-cb-4")

    aborted = _signed_callback(client, "tx-cb-4", {
        "transferId": "tx-cb-4", "transferState": "ABORTED",
        "errorInformation": {"errorCode": "3301",
                             "errorDescription": "payer expired"},
    }, FIXTURE_SECRET)
    assert aborted.status_code == 200
    assert aborted.json()["state"] == "void"

    # transferId in the body must match the path; unknown transfer is 404.
    mismatched = _signed_callback(client, "tx-cb-5",
                                  _fulfilment_payload("tx-other"),
                                  FIXTURE_SECRET)
    assert mismatched.status_code == 400
    ghost = _signed_callback(client, "tx-ghost", _fulfilment_payload("tx-ghost"),
                             FIXTURE_SECRET)
    assert ghost.status_code == 404
    unsupported = _signed_callback(client, "tx-cb-4", {
        "transferId": "tx-cb-4", "transferState": "RESERVED"}, FIXTURE_SECRET)
    assert unsupported.status_code == 409


def test_nibss_webhook_fails_closed_without_adapter() -> None:
    client = TestClient(create_app())  # no adapters wired
    resp = client.post("/mobility/v1/webhooks/nibss/ebills", json={
        "bill_reference": "BILL-X", "amount_kobo": 1,
    })
    assert resp.status_code == 503
