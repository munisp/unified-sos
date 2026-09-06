# mod-gis-luc — Land Use Charge Valuation

**WP-06 / EPIC-06 · Lot 4 · RT-03**

Automated LUC calculation by matching Sedona satellite building footprints to cadastral parcels; AI property valuation feeds from the lakehouse (Ray AVM, target >92% R² vs certified surveyor valuations).

- **Spatial job:** [`geospatial/sedona/unassessed_property_join.sql`](../../geospatial/sedona/unassessed_property_join.sql)
- **Events:** `ng.sos.gis.unassessed_property_discovered`
- **Acceptance:** 1M-parcel/footprint join < 5 s; first 5,000 automated LUC bills at Milestone 3
- **Stack:** Sedona · DataFusion · Ray
- **Deploys:** Ogun, Benue, Lagos, Nasarawa

## Implementation

`luc_app/` implements the valuation engine end-to-end, runnable and testable locally:

| Component | File | Notes |
|---|---|---|
| FastAPI service | [luc_app/main.py](luc_app/main.py) | `POST …/luc/valuation-runs`, `GET …/luc/bills[/{bill_id}]` (tenant-scoped) |
| Tariff policy packs | [luc_app/tariffs.py](luc_app/tariffs.py) | Per-state rates (kobo/m²/yr), statutory reliefs, minimum bills — config, not code |
| Calculator | [luc_app/calculator.py](luc_app/calculator.py) | Pure functions: `gross = max(area × rate, minimum)`, `net = gross × (1 − reliefs)` |
| Join-output consumer | [luc_app/ingestion.py](luc_app/ingestion.py) | `ng.sos.gis.unassessed_property_discovered` → assessments: COMPLIANT → none, UNASSESSED_IMPROVEMENT → full bill, UNREGISTERED_ENCROACHMENT → PROVISIONAL bill |
| Persistence seam | [luc_app/repository.py](luc_app/repository.py) | Tenant-isolated in-memory repo; production schema `db/migrations/0003_titling_and_luc.sql` |

## Run & test

```bash
pip install -r services/mod-gis-luc/requirements.txt
python3 -m pytest services/mod-gis-luc/                   # from repo root
cd services/mod-gis-luc && uvicorn luc_app.main:app --port 8002
```
