# Geospatial Analytics Jobs

Distributed spatial jobs for the dual-engine architecture (ADR-004): PostGIS for OLTP, Apache Sedona for planetary-scale analytics.

| Job | Engine | Purpose | Acceptance Metric |
|---|---|---|---|
| [sedona/unassessed_property_join.sql](sedona/unassessed_property_join.sql) | Sedona/Spark | Satellite building footprints ↔ cadastral parcels; discover unassessed properties | 1.2M-polygon join in 0.24 s (WP-14) |
| [sedona/ndvi_change_detection.py](sedona/ndvi_change_detection.py) | Ray + Sedona Raster | Sentinel-2 NDVI vegetation change — illegal mining / rosewood deforestation alerts | Canopy disturbance >0.5 ha flagged within 72 h (M5.2) |
