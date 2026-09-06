"""Journey 3 — offline POS edge-daemon -> mod-market sync (Stage 7.B).

    edge-daemon outbox (SQLite, Ed25519-signed tickets) -> reconnect sync
    through the in-process gateway shim -> mod-market ingestion
    -> replay yields server-side duplicates (idempotent dedupe)
    -> reconciliation against the in-memory ledger shows zero discrepancy.
"""

from __future__ import annotations

import importlib
import json

from fastapi import FastAPI

from conftest import STATE, SyncASGIClient, load_service_app

# Imported at module level (not inside the shim) so FastAPI can resolve the
# endpoint annotations under `from __future__ import annotations`.
from edge_daemon.models import (
    RecordAck,
    RevenueTicketPayload,
    SyncBatch,
    SyncBatchResult,
)

DEVICE_ID = "pos-e2e-01"
MARKET_ID = "mkt-e2e-01"
STALL_IDS = ["stall-a-001", "stall-a-002"]

#: Levy code 130 = market stallage (ledger/chart-of-accounts.md).
LEVY_STALLAGE = "130"

ACCT_POS_CLEARING = 300_001
ACCT_MARKET_REVENUE = 300_002

DAILY_FEE_KOBO = 50_000_00


def _gateway_shim(market_service, edge_sync_batch_model) -> FastAPI:
    """Minimal in-process sync gateway: edge-daemon SyncBatch -> mod-market.

    Production mapping: APISIX (mTLS edge termination) forwarding to the
    mod-market ingestion endpoint; acks are translated back into the edge
    daemon's SyncBatchResult shape unchanged.  The shim invokes the market
    ingestion service in-process (ASGI-in-ASGI would nest event loops);
    the market HTTP API is exercised directly for registration and reads.
    """
    app = FastAPI(title="s7e2e edge sync gateway shim")

    @app.post("/edge/v1/sync", response_model=SyncBatchResult)
    def sync(batch: SyncBatch) -> SyncBatchResult:
        edge_batch = edge_sync_batch_model(
            device_id=batch.device_id,
            records=[json.loads(r.model_dump_json()) for r in batch.records],
        )
        acks = [
            RecordAck(
                device_id=a.device_id,
                sequence=a.sequence,
                status=a.status,
                detail=a.detail,
            )
            for a in market_service.ingest_edge_batch(edge_batch)
        ]
        return SyncBatchResult(device_id=batch.device_id, acks=acks)

    return app


def _outbox_records(outbox, signer) -> list[dict]:
    """Read the exact signed records back out of the outbox (for replay)."""
    rows = outbox._conn.execute(
        "SELECT sequence, payload_json, signature FROM outbox "
        "WHERE device_id = ? ORDER BY sequence",
        (outbox.device_id,),
    ).fetchall()
    return [
        {
            "device_id": outbox.device_id,
            "sequence": row["sequence"],
            "payload": json.loads(row["payload_json"]),
            "signature": row["signature"],
            "signer_public_key": signer.public_key_b64(),
        }
        for row in rows
    ]


def test_offline_pos_journey(ledger, tmp_path):
    # In-process market app: HTTP client for the API legs plus the domain
    # service handle for the gateway shim's ingestion forward.
    market_app = load_service_app("market_pos", "mod-market")
    market_models = importlib.import_module("s7e2e_market_pos.app.models")
    market_client = SyncASGIClient(market_app)
    market_service = market_app.state.service
    # ------------------------------------------------------------------
    # 0. Market registry: one market, two stalls (daily stallage fee).
    # ------------------------------------------------------------------
    resp = market_client.post(
        "/markets",
        json={
            "market_id": MARKET_ID,
            "state_id": STATE,
            "name": "E2E Market",
            "market_type": "DAILY_MARKET",
            "lga": "Eti-Osa",
            "stall_capacity": 100,
        },
    )
    assert resp.status_code == 201, resp.text
    for i, stall_id in enumerate(STALL_IDS):
        resp = market_client.post(
            "/stalls",
            json={
                "stall_id": stall_id,
                "market_id": MARKET_ID,
                "block": "A",
                "number": f"{i + 1:03d}",
                "daily_fee_kobo": DAILY_FEE_KOBO,
            },
        )
        assert resp.status_code == 201, resp.text

    # ------------------------------------------------------------------
    # 1. Offline: POS daemon signs stallage tickets into its SQLite outbox.
    # ------------------------------------------------------------------
    from edge_daemon.crypto import SoftwareSigner
    from edge_daemon.outbox import Outbox
    from edge_daemon.sync import SyncEngine

    signer = SoftwareSigner.generate(DEVICE_ID)
    outbox = Outbox(tmp_path / "outbox.db", DEVICE_ID)
    for i, stall_id in enumerate(STALL_IDS):
        outbox.enqueue(
            signer,
            RevenueTicketPayload(
                state_id=STATE,
                bill_reference=f"BILL-E2E-{i:03d}",
                payer_id=f"trader-{i:03d}",
                levy_code=LEVY_STALLAGE,
                amount_kobo=DAILY_FEE_KOBO,
                collector_id="collector-01",
                location=stall_id,
            ),
        )
    assert len(outbox.pending()) == 2

    # ------------------------------------------------------------------
    # 2. Reconnect: sync the signed batch to mod-market via the gateway shim.
    # ------------------------------------------------------------------
    engine = SyncEngine(
        outbox,
        gateway_url="http://gateway.e2e",
        transport=SyncASGIClient(
            _gateway_shim(market_service, market_models.EdgeSyncBatch)
        )._transport,
        max_retries=0,
        sleep=lambda _: None,
    )
    assert engine.sync_all() == 2
    assert outbox.pending() == []

    # ------------------------------------------------------------------
    # 3. Replay: re-sending the same signed batch yields server-side
    #    duplicates (dedupe on (device_id, sequence)) — never double tickets.
    # ------------------------------------------------------------------
    replay_records = _outbox_records(outbox, signer)
    resp = market_client.post(
        "/tickets/ingest-edge-batch",
        json={"device_id": DEVICE_ID, "records": replay_records},
    )
    assert resp.status_code == 200, resp.text
    replay_acks = resp.json()
    assert [a["status"] for a in replay_acks] == ["duplicate", "duplicate"], replay_acks

    # Replays reference the originally accepted ticket IDs — one per stall.
    ticket_ids = {a["ticket_id"] for a in replay_acks}
    assert len(ticket_ids) == len(STALL_IDS)
    tickets = []
    for ticket_id in sorted(ticket_ids):
        resp = market_client.get(f"/tickets/{ticket_id}")
        assert resp.status_code == 200, resp.text
        ticket = resp.json()
        assert ticket["origin"] == "OFFLINE_EDGE"
        tickets.append(ticket)
    total_kobo = sum(t["amount_kobo"] for t in tickets)
    assert total_kobo == DAILY_FEE_KOBO * len(STALL_IDS)

    # ------------------------------------------------------------------
    # 4. Reconciliation: post the accepted ticket amounts to the in-memory
    #    ledger (rev-core fake semantics) and verify zero discrepancy
    #    against mod-market's ticketed totals.
    # ------------------------------------------------------------------
    ledger.create_account(ACCT_POS_CLEARING)
    ledger.create_account(ACCT_MARKET_REVENUE)
    ledger.seed_account(ACCT_POS_CLEARING, total_kobo)
    ledger.create_transfers(
        [
            {
                "id": 9_000 + i,
                "debit": ACCT_POS_CLEARING,
                "credit": ACCT_MARKET_REVENUE,
                "amount": t["amount_kobo"],
            }
            for i, t in enumerate(tickets)
        ]
    )
    assert ledger.balance(ACCT_MARKET_REVENUE) == total_kobo  # zero discrepancy
    assert ledger.balance(ACCT_POS_CLEARING) == 0
