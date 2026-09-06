# mod-transport-wim — Weigh-in-Motion & Corridor Haulage

**WP-08 / EPIC-09 · Lot 6 · RT-04**

High-speed WIM piezo/bending-plate sensor ingestion, ANPR OCR pipelines, automated overload fine issuance, corridor tolls. Rust/Fluvio edge agents at weighbridge nodes → APISIX → TigerBeetle fine ledger.

- **Events:** `ng.sos.mining.weighbridge_reading` (shared envelope)
- **Acceptance:** overload detected and fine issued < 3 s at > 80 km/h; WIM accuracy ±3% up to 100 km/h all-weather; e-manifest verification < 5 s at checkpoints
- **Stack:** Rust · Fluvio · TigerBeetle · APISIX · Flutter (field POS)
- **Deploys:** Ogun (Sagamu-Interchange, Abeokuta-Ibadan, Ewekoro), Lagos (Lekki/Apapa), Nasarawa, Benue
