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

## v3.0 alignment — SedonaDB, H3, GeoParquet/Delta storage

The v3.0 business specification extends the dual-engine architecture
(ADR-004) with the following components **[DERIVED]** (targets from the v3.0
pack + geospatial research summary; adoption is tracked in
`docs/delivery/rtm-v3.md` GEO-02/03/04):

- **SedonaDB (Rust + Apache DataFusion)** — embedded single-node spatial
  analytics engine complementing the Sedona/Spark cluster. Intended for
  lakehouse-edge and per-state analytic jobs where a full Spark cluster is
  not justified.
- **H3 hexagonal indexing** — deforestation alerts and industrial telemetry
  geofences carry H3 cell IDs alongside polygon geometry, enabling
  cell-based aggregation, cross-source dedup, and SLA bucketing.
- **GeoParquet / Delta Lake (WKB geometry)** — canonical Silver→Gold
  lakehouse storage for spatial tables (`sedona_building_footprints`,
  `sedona_cadastral_parcels`, NDVI alert polygons). Geometry is stored as
  WKB per the GeoParquet 1.x specification with CRS metadata.
- **State GIS agency integration (seams)**: **NAGIS** (Nasarawa), **TAGIS**
  (Taraba), **LASGIS** (Lagos), **BENGIS** (Benue) — parcel, orthophoto and
  ground-control exchange is an adapter seam; no live agency feed is
  connected in this repository **[GAP]**.

### v3.0 spatial join benchmark **[DERIVED]**

From the v3.0 geospatial research summary; not re-executed in this repo —
treat as projection until a reproducible benchmark harness is committed:

| Workload | PostGIS R-Tree | SedonaDB | Speedup |
|---|---|---|---|
| 10M-row spatial join (footprints ↔ parcels) | ~6.4 s | ~0.24 s | ~26× |

The committed acceptance metric for the production Sedona/Spark job remains
the WP-14 figure above (1.2M-polygon join in 0.24 s).
