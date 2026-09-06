# mod-market — Commercial Markets & Digital Stall Titling

**WP-12 / EPIC-14 · Lot 7 · RT-04**

Digital market stall cadastre, concession lease contracts, automated micro-tenancy billing, trader daily stallage micro-collection via USSD/POS with TigerBeetle escrow, POS trader agent network.

- **Acceptance:** > 95% collection rate across major urban markets; middleman cash leakage eliminated
- **Stack:** Go · PostGIS · TigerBeetle · Form.io
- **Deploys:** Osun (Osogbo Central, 35,000 traders), Nasarawa (Mararaba), all 6

## Reference Implementation (Python / FastAPI)

- `app/models.py` — market/stall/trader registry, daily stallage tickets
  (unique per stall+day, transfer code 130), dispute events, and the
  edge-daemon offline batch contract (`EdgeSyncBatch` — wire-compatible with
  `edge/edge-daemon`).
- `app/service.py` — double-charge prevention, signed offline-ticket ingestion
  with Ed25519 verification + `(device_id, sequence)` idempotent dedupe, and a
  dispute workflow whose audit trail is **append-only by construction**
  (arbitration-grade; no update/delete paths).
- `tests/test_edge_ingestion.py` — generates genuine signed batches with the
  real edge daemon to prove the POS → service wire contract.

### Run / test

```bash
cd services/mod-market
pip install fastapi httpx pydantic cryptography pytest uvicorn
python3 -m pytest -q
uvicorn app.main:app --port 8003
```
