# Deep Geospatial Analytics Engine — Apache Sedona & PostGIS

## Dual Spatial Architecture

Subnational administration needs both **instantaneous single-record queries** (does this Abeokuta building overlap a gazetted transmission-line setback?) and **massive parallel spatial computation** (join 2.5 million Lagos building footprints against 800,000 cadastral parcels — hours in standard PostGIS, seconds in SOS).

| Functional Layer | Technology | Spatial Representation | Performance Profile |
|---|---|---|---|
| Transactional Spatial OLTP | PostgreSQL 16+ / PostGIS 3.4+ / pgRouting | Geometry/Geography WKB, GiST & SP-GiST indexes | < 5 ms point-in-polygon; 10,000 req/s |
| Vector Tile Streaming API | Martin Tile Server (Rust) | Mapbox Vector Tiles (MVT), gzip | < 15 ms tile generation directly from PostGIS |
| Distributed Spatial Lakehouse | Apache Sedona 1.8 (Spark + DataFusion) | GeoParquet (GeoArrow), R-Tree spatial RDDs | 1.2M-polygon spatial join in 0.24 s |
| Satellite Remote Sensing AI | Python Ray + Sedona Raster + TorchGeo | Cloud-Optimized GeoTIFF, Sentinel-2 10 m L2A | Statewide NDVI change detection in 8 min |

PostGIS is the **operational layer** (sub-millisecond spatial CRUD, title lookups, deed boundary updates); Apache Sedona/SedonaDB is the **distributed analytical layer** (`ST_Contains`, `ST_Intersects` at scale, NDVI vegetation change detection for illegal mining and rosewood deforestation, automated unassessed-property discovery). Continuous async CDC flows from PostGIS to Delta Lake via Debezium/Kafka (ADR-004).

## Reference Query — Unassessed Property Discovery (Ogun industrial zones)

```sql
-- Executed on Apache Sedona / Spark distributed cluster
SELECT
  b.building_footprint_id,
  b.estimated_area_sqm,
  p.parcel_uin,
  p.owner_stin,
  p.assessed_annual_luc_kobo,
  CASE
    WHEN p.parcel_uin IS NULL THEN 'UNREGISTERED_ENCROACHMENT'
    WHEN p.assessed_annual_luc_kobo = 0 THEN 'UNASSESSED_IMPROVEMENT'
    ELSE 'COMPLIANT'
  END AS audit_status
FROM sedona_building_footprints b
FULL OUTER JOIN sedona_cadastral_parcels p
  ON ST_Intersects(b.geom, p.geom)
WHERE b.tenant_state_id = 'ogun';
```

Executable job: [`geospatial/sedona/unassessed_property_join.sql`](../../../geospatial/sedona/unassessed_property_join.sql).

## Cadastral Precision & Standards

- Surveyor beacons stored in UTM Minna Datum (EPSG:26391/26392/26393) and WGS84 (EPSG:4326).
- Automated topological overlap checking against gazetted reserves, road setbacks, and existing titles.
- Zero overlapping-polygon tolerance on newly registered parcels; sub-meter spatial precision (acceptance gate M4.2).

DDL with Row-Level Security tenancy isolation: [`db/migrations/0001_cadastre.sql`](../../../db/migrations/0001_cadastre.sql).
