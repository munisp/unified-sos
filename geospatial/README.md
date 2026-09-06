# Geospatial Analytics Jobs

Distributed spatial jobs for the dual-engine architecture (ADR-004): PostGIS for OLTP, Apache Sedona for planetary-scale analytics.

| Job | Engine | Purpose | Acceptance Metric |
|---|---|---|---|
| [sedona/unassessed_property_join.sql](sedona/unassessed_property_join.sql) | Sedona/Spark | Satellite building footprints ↔ cadastral parcels; discover unassessed properties | 1.2M-polygon join in 0.24 s (WP-14) |
| [sedona/ndvi_change_detection.py](sedona/ndvi_change_detection.py) | Ray + Sedona Raster | Sentinel-2 NDVI vegetation change — illegal mining / rosewood deforestation alerts | Canopy disturbance >0.5 ha flagged within 72 h (M5.2) |

## Production mode (Sedona cluster)

The files under `sedona/` are the **canonical production jobs**:

- **`sedona/unassessed_property_join.sql`** — run on the Apache Sedona 1.8
  (Spark + DataFusion) cluster over the GeoParquet lakehouse tables
  `sedona_building_footprints` and `sedona_cadastral_parcels` (R-Tree spatial
  RDDs). Parameterize `tenant_state_id` per state. Output rows are published
  to `ng.sos.gis.unassessed_property_discovered` (contracts/asyncapi/platform-events.yaml)
  and consumed by `services/mod-gis-luc` to generate LUC assessments.
- **`sedona/ndvi_change_detection.py`** — Ray + Sedona Raster over Sentinel-2
  10 m L2A Cloud-Optimized GeoTIFFs; TorchGeo model classifies suspected
  activity; alerts are polygonized and stored to the Silver→Gold lakehouse.

## Local verification mode (CI / laptops, no cluster required)

The `local/` package re-executes the **same business logic** with
shapely/NumPy over the committed fixtures, so pull-request CI can verify
classification behaviour:

```bash
pip install -r geospatial/requirements.txt
python3 -m pytest geospatial/        # from the repo root
```

| Local runner | Verifies | Fixtures |
|---|---|---|
| [local/unassessed_property_join.py](local/unassessed_property_join.py) | ST_Intersects join + `UNREGISTERED_ENCROACHMENT` / `UNASSESSED_IMPROVEMENT` / `COMPLIANT` CASE classification | [fixtures/building_footprints.geojson](fixtures/building_footprints.geojson), [fixtures/cadastral_parcels.geojson](fixtures/cadastral_parcels.geojson) (one of each status + a cross-tenant footprint) |
| [local/ndvi_change_detection.py](local/ndvi_change_detection.py) | NDVI delta thresholding, connected-component polygonize, ≥0.5 ha area filter, alert schema | [fixtures/synthetic_ndvi.py](fixtures/synthetic_ndvi.py) (deterministic synthetic Sentinel-2 band pair) |

The local runners accept GeoJSON always, and GeoParquet when `geopandas` +
`pyarrow` are installed. Production-only concerns (COG raster IO, SCL cloud
masking, TorchGeo classification, distributed scheduling) are documented in
the module docstrings — do not treat local heuristic outputs as production
classifications.
