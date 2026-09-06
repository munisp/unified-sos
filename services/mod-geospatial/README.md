# mod-geospatial

FastAPI orchestration service for geospatial datasets, processing jobs, H3
indexing, GeoLibre project generation, and lakehouse publication
(SPEC-GEOSPATIAL §4.1). Port **8013** in local deployment.

## Local vs production modes

Everything runs deterministically in local/test mode (default,
`GEOSPATIAL_MODE=local`) with **no external infrastructure**:

| Concern | Local/test (implemented, tested) | Production seam (fails closed) |
| --- | --- | --- |
| Repository | `InMemoryGeospatialRepository` | `PostGISGeospatialRepository` — requires `psycopg` + `GEOSPATIAL_POSTGIS_DSN`, RLS schema in `db/migrations/0006_geospatial.sql` |
| H3 indexing | `LocalH3Adapter` — **clearly labeled** geohash-like cells (`LOCAL-GH<res>-…`, backend name `local-geohash-fallback`); **not real H3** | `RealH3Adapter` with the optional `h3` package |
| GeoParquet export | deterministic JSON fallback with `.json` suffix, marked `local_fallback` | real GeoParquet (WKB geometry + CRS metadata) via optional `geopandas`+`pyarrow` |
| GeoLibre projects | deterministic local `.geolibre.json` builder | optional `geolibre` package primitives |
| GeoLibre processing | deterministic pure-Python tools (`buffer`, `centroid`, `area`) | optional `geolibre_wasm.run_tool()` / `GEOLIBRE_WASM` |
| Sedona jobs | local runners in `geospatial/local/` (reused read-only, unmodified) | `SedonaAdapter` — requires `GEOSPATIAL_SEDONA_ENDPOINT` |

Any production adapter missing its package, endpoint, or credentials raises
`AdapterUnavailableError` (HTTP 503) instead of degrading silently.

## API

- `GET /healthz`
- `POST /api/v1/states/{state_id}/geospatial/datasets` — register dataset
  metadata (`CADASTRE`, `BUILDING_FOOTPRINTS`, `NDVI_ALERTS`,
  `FOREST_RESERVES`, `TRANSPORT_CORRIDORS`, `MARKET_BOUNDARIES`, `MINING_SITES`)
- `GET …/datasets`, `GET …/datasets/{dataset_id}`
- `POST …/jobs` — `UNASSESSED_PROPERTY_JOIN`, `NDVI_CHANGE_DETECTION`,
  `H3_AGGREGATION`, `GEOPARQUET_EXPORT`, `GEOLIBRE_PROJECT_BUILD`
- `GET …/jobs`, `GET …/jobs/{job_id}`, `POST …/jobs/{job_id}/run`
- `POST …/h3/index` — deterministic cell IDs for a GeoJSON geometry
- `POST …/geolibre/projects`, `GET …/geolibre/projects/{project_id}`
- `GET …/audit` — tenant-scoped, hash-only, hash-chained audit

## Guarantees

- **Tenant isolation** — every repository/service call requires
  `tenant_state_id`; cross-tenant reads return 404.
- **Job state machine** — `QUEUED → RUNNING → SUCCEEDED | FAILED`; illegal
  transitions are rejected (HTTP 409).
- **Hash-only audit** — entries are SHA-256 hash-chained and never contain
  raw geometry for SENSITIVE datasets (hashes + object URIs only).
- **GeoLibre redaction** — project layers reference only self-hosted or
  object-storage URLs; `*.geolibre.app` hosted URLs and credentialed URLs are
  rejected. SENSITIVE datasets contribute metadata-only layers. No raw
  landowner PII/NIN/biometrics ever leaves the service.
- **Reuse** — local `UNASSESSED_PROPERTY_JOIN` and `NDVI_CHANGE_DETECTION`
  reuse the tested runners in `geospatial/local/` (identical output
  semantics); the files are not modified.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8013
pytest            # from this directory
```

Docker build (context = repository root):

```bash
docker build -f services/mod-geospatial/Dockerfile .
```
