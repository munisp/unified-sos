# mod-forestry — Timber Provenance & Deforestation Alerts

**WP-07 / EPIC-08 · Lot 5 · RT-03**

UHF RFID nail-tag provenance for logs, ranger GPS scanners, timber transit permits, and Sentinel-2 NDVI canopy change detection for illegal logging enforcement (rosewood cartel suppression).

- **Job:** [`geospatial/sedona/ndvi_change_detection.py`](../../geospatial/sedona/ndvi_change_detection.py)
- **Events:** `ng.sos.forestry.untagged_timber_alert`
- **Acceptance:** canopy disturbance > 0.5 ha flagged within 72 h of ingest; automated alerts on untagged haulage
- **Stack:** Python · Sedona · PostGIS · Delta Lake · OpenCTI
- **Deploys:** Taraba (rosewood), Ogun (emissions IoT variant), Osun, Benue

## Implementation (Python / FastAPI)

- `app/models.py` — UHF RFID nail-tag registry, provenance events,
  NDVI deforestation alerts (severity: `ACTIONABLE` > 0.5 ha), stumpage invoices.
- `app/service.py` — provenance state machine (ISSUED → HARVESTED → IN_TRANSIT →
  MILLED; SEIZED terminal), licensed-coupe enforcement, transit-permit requirement,
  stumpage billing hook on harvest, untagged-haulage alerts published to
  `ng.sos.forestry.untagged_timber_alert`.
- `app/main.py` — FastAPI HTTP surface incl. `/alerts/deforestation` ingestion
  endpoint for the Sedona NDVI job.

### Run / test

```bash
cd services/mod-forestry
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q
uvicorn app.main:app --port 8002
```
