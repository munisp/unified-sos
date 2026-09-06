# mod-agri-waybill — Agribusiness Supply Chain & E-Waybill

**WP-09 / EPIC-11 · Lot 6/9 · RT-04**

Digital produce inspection and e-waybills, haulage checkpoint verification, agro-hub warehouse receipt tokenization, commodity collateral clearing, EUDR-compliant deforestation-free export provenance (cocoa/tea traceability passports).

- **Acceptance:** waybills verified at state borders < 10 s via QR; farmers receive bank credit against e-receipts
- **Stack:** Python · Go · Dapr · Redis · PostGIS · Flink · TigerBeetle
- **Deploys:** Benue (food basket), Taraba (Mambilla tea, cross-border), Nasarawa (agro-hubs), Osun (cocoa EUDR)

## Reference Implementation (Python / FastAPI)

- `app/models.py` — e-waybills (per-consignment levy, transfer code 140),
  checkpoint tracking events, warehouse receipts.
- `app/service.py` — QR payload issuance/verification
  (`SOSWB1.<b64-json>.<b64-hmac-sha256>`; HMAC key from secret store in
  production), consignment tracking state flow, warehouse-receipt issuance
  gated on delivered consignments with quantity/produce-type consistency.
- `app/main.py` — FastAPI surface; `/waybills/verify` is the border-checkpoint
  QR endpoint (acceptance < 10 s; in-process verification ≈ ms).

### Run / test

```bash
cd services/mod-agri-waybill
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q
uvicorn app.main:app --port 8004
```
