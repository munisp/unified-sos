# Edge — Offline-First POS & Checkpoint Design

Subnational edge resilience is a system design goal: POS revenue terminals, transit weighbridges, and border toll gates must operate offline during rural connectivity drops (Taraba/Benue/Nasarawa corridors) and sync reliably on reconnection.

## Architecture

1. **Embedded SQLite/Rust edge daemon** on ruggedized Android POS terminals (solar-powered at remote checkpoints).
2. **Local cryptographic signing** — revenue tickets and digital tax receipts signed using the terminal's hardware secure element (SE).
3. **Local cache** — ≥ 5,000 signed transactions buffered offline (WP-05 acceptance).
4. **Sync** — asynchronous batch sync to the APISIX gateway via **mutual TLS** on cellular reconnect; Fluvio edge streaming pipeline with offline-sync reconciliation.

## SLOs

| Metric | Target |
|---|---|
| POS sync availability | 99.90% (offline-first) |
| Sync latency on connect | < 3 s |
| RPO / RTO | RPO < local / RTO < 1 min |
| Checkpoint e-manifest verification | < 5 s per vehicle |

## Hardware Profiles

- Ruggedized Android POS with BLE thermal printers + biometric validation (WP-08 M6.3)
- WIM edge IoT controllers with solar backup (WP-08 M6.1)
- ANPR camera corridors streaming via Fluvio
- Solar border kiosks (Kashimbila & Gembu, Taraba)

## Reference Implementation — `edge-daemon/`

`edge-daemon/` is the **verified reference implementation of the edge sync
protocol**, written in Python so the full flow is testable locally in CI.
The production target remains the **Rust daemon on Android POS terminals**
(embedded SQLite + hardware SE); the protocol — canonical signing bytes,
signature envelope, `(device_id, sequence)` dedupe key, batch sync semantics —
is defined and verified here and must be matched 1:1 by the Rust client.

What it implements:

| Component | File | Notes |
|---|---|---|
| Payload models | `edge_daemon/models.py` | Revenue tickets & e-waybills aligned with `contracts/asyncapi/` (kobo integers, state enums) |
| Signing | `edge_daemon/crypto.py` | Ed25519 — **stand-in for the hardware SE**; swap `DeviceSigner.sign` for an SE-backed call in production |
| Outbox | `edge_daemon/outbox.py` | SQLite WAL, crash-safe, monotonic per-device sequence, ≥5,000 pending records (default 10,000) |
| Sync engine | `edge_daemon/sync.py` | `httpx` batch push, exponential backoff + full jitter, mTLS hooks (`cert=`/`verify=`), resumable after restart |
| Fake gateway | `edge_daemon/gateway.py` | In-process server double enforcing signature verification, idempotent dedupe and out-of-order rejection |

### Run / test

```bash
cd edge/edge-daemon
pip install fastapi httpx pydantic cryptography pytest
python3 -m pytest -q
```

The suite covers: offline buffering of 5,000+ signed records, signature
verification and tamper rejection, server-side duplicate/out-of-order
rejection, crash recovery (DB reopened mid-queue, no sequence reuse),
retry-with-backoff behaviour, and a sync-latency sanity check against the
< 3 s sync SLO.

### mTLS note

`SyncEngine` accepts standard `httpx` client-TLS arguments
(`cert=("device.crt", "device.key")`, `verify="ca-bundle.pem"`). Tests inject
`SyncASGITransport`, an in-process ASGI transport, so no sockets or
certificates are needed to verify protocol behaviour end-to-end.
