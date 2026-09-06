"""Fake in-process sync gateway (test double for the APISIX + ingestion tier).

Implements the server side of the edge sync protocol:

1. **Signature verification** — every record's Ed25519 signature is verified
   against the embedded device public key; bad signatures are rejected.
2. **Idempotent dedupe** — ``(device_id, sequence)`` already accepted returns
   ``duplicate`` without side effects (safe client retries).
3. **Sequence integrity** — the gateway expects strictly increasing sequences
   per device. A record arriving *below or equal to* the high-water mark that
   was never seen, or a *gap skip* (sequence jumps ahead), is rejected as
   out-of-order, so a tampered or reordered stream cannot be committed.

In production this logic lives behind APISIX (mTLS termination) and the
reconciliation consumer on the Fluvio edge stream; state is Postgres, here it
is an in-memory dict so tests are fully local.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple

import httpx
from fastapi import FastAPI

from .crypto import verify
from .models import RecordAck, SignedRecord, SyncBatch, SyncBatchResult


class FakeGatewayState:
    """Durably-accepted record store + per-device high-water marks."""

    def __init__(self) -> None:
        self.accepted: Dict[Tuple[str, int], SignedRecord] = {}
        self.high_water: Dict[str, int] = {}

    def handle(self, batch: SyncBatch) -> SyncBatchResult:
        acks: List[RecordAck] = []
        for rec in batch.records:
            acks.append(self._handle_record(rec))
        return SyncBatchResult(device_id=batch.device_id, acks=acks)

    def _handle_record(self, rec: SignedRecord) -> RecordAck:
        key = (rec.device_id, rec.sequence)

        if not verify(rec.signer_public_key, rec.signing_bytes(), rec.signature):
            return RecordAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="rejected",
                detail="invalid signature",
            )

        if key in self.accepted:
            return RecordAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="duplicate",
                detail="idempotent replay of (device_id, sequence)",
            )

        hw = self.high_water.get(rec.device_id, 0)
        if rec.sequence <= hw or rec.sequence > hw + 1:
            return RecordAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="rejected",
                detail=f"out-of-order: expected sequence {hw + 1}",
            )

        self.accepted[key] = rec
        self.high_water[rec.device_id] = rec.sequence
        return RecordAck(
            device_id=rec.device_id, sequence=rec.sequence, status="accepted"
        )


def create_gateway_app(state: FakeGatewayState | None = None) -> FastAPI:
    """Build the fake gateway FastAPI app (for httpx.ASGITransport / TestClient)."""
    state = state or FakeGatewayState()
    app = FastAPI(title="SOS Fake Edge Gateway")
    app.state.gateway = state

    @app.post("/edge/v1/sync", response_model=SyncBatchResult)
    def sync(batch: SyncBatch) -> SyncBatchResult:
        return state.handle(batch)

    @app.get("/edge/v1/records")
    def records() -> dict:
        return {
            "accepted": len(state.accepted),
            "high_water": state.high_water,
        }

    return app


class SyncASGITransport(httpx.BaseTransport):
    """Synchronous ``httpx`` transport over an in-process ASGI app.

    Lets the (synchronous) :class:`~edge_daemon.sync.SyncEngine` talk to the
    fake gateway without a network socket. In production this slot is real
    mTLS over HTTPS via ``httpx.Client(cert=..., verify=...)``.
    """

    def __init__(self, app: FastAPI) -> None:
        self._inner = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        async def _roundtrip() -> httpx.Response:
            resp = await self._inner.handle_async_request(request)
            chunks = [chunk async for chunk in resp.stream]
            body = b"".join(chunks)
            return httpx.Response(
                status_code=resp.status_code,
                headers=resp.headers,
                content=body,
                extensions=resp.extensions,
            )

        return asyncio.run(_roundtrip())
