# mod-forestry — Timber Provenance & Deforestation Alerts

**WP-07 / EPIC-08 · Lot 5 · RT-03**

UHF RFID nail-tag provenance for logs, ranger GPS scanners, timber transit permits, and Sentinel-2 NDVI canopy change detection for illegal logging enforcement (rosewood cartel suppression).

- **Job:** [`geospatial/sedona/ndvi_change_detection.py`](../../geospatial/sedona/ndvi_change_detection.py)
- **Events:** `ng.sos.forestry.untagged_timber_alert`
- **Acceptance:** canopy disturbance > 0.5 ha flagged within 72 h of ingest; automated alerts on untagged haulage
- **Stack:** Python · Sedona · PostGIS · Delta Lake · OpenCTI
- **Deploys:** Taraba (rosewood), Ogun (emissions IoT variant), Osun, Benue
