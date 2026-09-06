-- SOS Geospatial Job: Unassessed Property Discovery
-- Engine: Apache Sedona on Spark/DataFusion (GeoParquet lakehouse, R-Tree spatial RDDs)
-- WP-06/WP-14 · EPIC-06/EPIC-16 · Acceptance: 1.2M-polygon join in 0.24s;
-- 1M parcels vs footprints in <5s (M4.1)
--
-- Matches satellite-derived building polygons against cadastral parcel boundaries
-- to discover unassessed properties across state industrial zones.

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
WHERE b.tenant_state_id = 'ogun';   -- parameterize per state tenant

-- Findings are published to: ng.sos.gis.unassessed_property_discovered
-- (see contracts/asyncapi/platform-events.yaml)
