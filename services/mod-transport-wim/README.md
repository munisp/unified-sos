# mod-transport-wim — Weigh-in-Motion & Corridor Haulage

**WP-08 / EPIC-09 · Lot 6 · RT-04**

High-speed WIM piezo/bending-plate sensor ingestion, ANPR OCR pipelines, automated overload fine issuance, corridor tolls. Rust/Fluvio edge agents at weighbridge nodes → APISIX → TigerBeetle fine ledger.

- **Events:** `ng.sos.mining.weighbridge_reading` (shared envelope)
- **Acceptance:** overload detected and fine issued < 3 s at > 80 km/h; WIM accuracy ±3% up to 100 km/h all-weather; e-manifest verification < 5 s at checkpoints
- **Stack:** Rust · Fluvio · TigerBeetle · APISIX · Flutter (field POS)
- **Deploys:** Ogun (Sagamu-Interchange, Abeokuta-Ibadan, Ewekoro), Lagos (Lekki/Apapa), Nasarawa, Benue

## Reference Implementation (Python / FastAPI)

- `app/models.py` — per-corridor gazetted limits (policy-pack config), WIM
  readings, overload verdicts, fine assessments (transfer code 120), ANPR
  events, e-manifests.
- `app/service.py` — axle/tandem/GVW overload detection with instrument
  tolerance, automatic fine assessment, ANPR ↔ WIM plate correlation
  (±30 s window), and publication of every reading on the shared
  `ng.sos.mining.weighbridge_reading` envelope.
- `app/main.py` — FastAPI surface; `/manifests/{id}/verify` is the checkpoint
  e-manifest endpoint (SLO < 5 s per vehicle; in-process ≈ ms).

### Run / test

```bash
cd services/mod-transport-wim
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q
uvicorn app.main:app --port 8005
```
