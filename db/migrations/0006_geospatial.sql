-- SOS Migration 0006 — GEO geospatial platform domain
-- v3.0 · PostgreSQL 16+ · PostGIS · Tenant-isolated via Row-Level Security
-- Complements 0001–0005. PostGIS remains the OLTP system of record for
-- tenant-isolated geospatial datasets, processing jobs, and map project
-- metadata; Apache Sedona/lakehouse handles analytical-scale workloads.
-- GeoLibre is a self-hosted workbench only — never the system of record.

CREATE SCHEMA IF NOT EXISTS geospatial;

-- ---------------------------------------------------------------------------
-- Registered geospatial datasets (metadata only; payloads live in object
-- storage / lakehouse and are referenced by URI).
-- ---------------------------------------------------------------------------
CREATE TABLE geospatial.datasets (
    dataset_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    dataset_type     VARCHAR(32) NOT NULL,         -- PARCEL | RASTER | VECTOR | H3_INDEX | GEOPARQUET ...
    name             VARCHAR(256) NOT NULL,
    source_uri       VARCHAR(512) NOT NULL,
    crs              VARCHAR(32) NOT NULL DEFAULT 'EPSG:4326',
    geometry_metadata JSONB NOT NULL DEFAULT '{}',
    sensitivity      VARCHAR(16) NOT NULL DEFAULT 'INTERNAL',  -- PUBLIC | INTERNAL | RESTRICTED
    h3_resolution    INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE geospatial.dataset_features (
    feature_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id       UUID NOT NULL REFERENCES geospatial.datasets (dataset_id),
    tenant_state_id  VARCHAR(10) NOT NULL,
    feature_ref      VARCHAR(128) NOT NULL,
    geom             GEOMETRY(Geometry, 4326),
    h3_cells         TEXT[] NOT NULL DEFAULT '{}',
    attributes_hash  VARCHAR(128),                 -- hash of attribute payload; no raw PII
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE geospatial.processing_jobs (
    job_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    job_type         VARCHAR(48) NOT NULL,         -- H3_INDEX | SPATIAL_JOIN | NDVI | GEOLIBRE_PROJECT | AGENCY_SYNC ...
    status           VARCHAR(16) NOT NULL DEFAULT 'QUEUED',  -- QUEUED | RUNNING | COMPLETED | FAILED
    input_dataset_ids UUID[] NOT NULL DEFAULT '{}',
    parameters       JSONB NOT NULL DEFAULT '{}',
    output_uri       VARCHAR(512),
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE TABLE geospatial.job_results (
    result_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id           UUID NOT NULL REFERENCES geospatial.processing_jobs (job_id),
    tenant_state_id  VARCHAR(10) NOT NULL,
    result_type      VARCHAR(32) NOT NULL,         -- GEOPARQUET | H3_CELLS | PROJECT_JSON | METRICS ...
    result_uri       VARCHAR(512) NOT NULL,
    metrics          JSONB NOT NULL DEFAULT '{}',
    result_hash      VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- GeoLibre project metadata: .geolibre.json artifacts built for the
-- self-hosted workbench. Stores URI + hash + redaction level only.
CREATE TABLE geospatial.geolibre_projects (
    project_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    name             VARCHAR(256) NOT NULL,
    project_uri      VARCHAR(512) NOT NULL,
    project_hash     VARCHAR(128) NOT NULL,
    redaction_level  VARCHAR(16) NOT NULL DEFAULT 'FULL',  -- FULL | PARTIAL | NONE
    source_job_id    UUID REFERENCES geospatial.processing_jobs (job_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Inter-agency geodata synchronisation links (adapter seams; fail closed
-- when endpoints/credentials are unavailable).
CREATE TABLE geospatial.agency_sync_links (
    link_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    agency           VARCHAR(64) NOT NULL,
    direction        VARCHAR(8) NOT NULL,          -- PUSH | PULL
    endpoint_uri     VARCHAR(512) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'PENDING',  -- PENDING | ACTIVE | FAILED | DISABLED
    last_synced_at   TIMESTAMPTZ,
    metadata         JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_geo_datasets_tenant_type   ON geospatial.datasets (tenant_state_id, dataset_type);
CREATE INDEX idx_geo_features_tenant        ON geospatial.dataset_features (tenant_state_id, dataset_id);
CREATE INDEX idx_geo_features_geom          ON geospatial.dataset_features USING GiST (geom);
CREATE INDEX idx_geo_features_h3            ON geospatial.dataset_features USING GIN (h3_cells);
CREATE INDEX idx_geo_jobs_tenant_status     ON geospatial.processing_jobs (tenant_state_id, status);
CREATE INDEX idx_geo_results_tenant_job     ON geospatial.job_results (tenant_state_id, job_id);
CREATE INDEX idx_geo_projects_tenant        ON geospatial.geolibre_projects (tenant_state_id);
CREATE INDEX idx_geo_sync_tenant_status     ON geospatial.agency_sync_links (tenant_state_id, status);

-- ---------------------------------------------------------------------------
-- Row-Level Security — identical tenant-isolation convention as 0001–0005
-- ---------------------------------------------------------------------------
ALTER TABLE geospatial.datasets            ENABLE ROW LEVEL SECURITY;
ALTER TABLE geospatial.dataset_features    ENABLE ROW LEVEL SECURITY;
ALTER TABLE geospatial.processing_jobs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE geospatial.job_results         ENABLE ROW LEVEL SECURITY;
ALTER TABLE geospatial.geolibre_projects   ENABLE ROW LEVEL SECURITY;
ALTER TABLE geospatial.agency_sync_links   ENABLE ROW LEVEL SECURITY;

CREATE POLICY datasets_tenant_isolation ON geospatial.datasets
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY dataset_features_tenant_isolation ON geospatial.dataset_features
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY processing_jobs_tenant_isolation ON geospatial.processing_jobs
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY job_results_tenant_isolation ON geospatial.job_results
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY geolibre_projects_tenant_isolation ON geospatial.geolibre_projects
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY agency_sync_links_tenant_isolation ON geospatial.agency_sync_links
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
