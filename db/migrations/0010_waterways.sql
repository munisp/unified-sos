-- SOS Migration 0010 — Waterways domain (mod-waterways)
-- PostgreSQL 16+ / PostGIS 3.4+ · Tenant-isolated via Row-Level Security
-- Ferry e-ticketing + sand-dredging volumetrics. Money in integer kobo.
-- Tickets are idempotent on (tenant, idempotency_key); surveys deduped on
-- dedupe_key. Royalty assessments form a hash-chained audit ledger.

CREATE SCHEMA IF NOT EXISTS waterways;

-- ---------------------------------------------------------------------------
-- Route/jetty registry and scheduled trips
-- ---------------------------------------------------------------------------
CREATE TABLE waterways.routes (
    route_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    name             VARCHAR(128) NOT NULL,
    origin_jetty     VARCHAR(128) NOT NULL,
    destination_jetty VARCHAR(128) NOT NULL,
    distance_km      INTEGER NOT NULL CHECK (distance_km > 0)
);

CREATE TABLE waterways.trips (
    trip_id          VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    route_id         VARCHAR(64) NOT NULL REFERENCES waterways.routes (route_id),
    vessel           VARCHAR(128) NOT NULL,
    capacity         INTEGER NOT NULL CHECK (capacity > 0),
    departure        TIMESTAMPTZ NOT NULL,
    status           VARCHAR(12) NOT NULL DEFAULT 'scheduled',  -- scheduled | departed
    manifest_locked  BOOLEAN NOT NULL DEFAULT FALSE,            -- safety rule: locked at departure
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Tickets — idempotent on (tenant, idempotency_key)
-- ---------------------------------------------------------------------------
CREATE TABLE waterways.tickets (
    ticket_id        VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    trip_id          VARCHAR(64) NOT NULL REFERENCES waterways.trips (trip_id),
    passenger_name   VARCHAR(256) NOT NULL,
    fare_kobo        BIGINT NOT NULL CHECK (fare_kobo >= 0),
    qr_ref           VARCHAR(64) NOT NULL UNIQUE,   -- QR-style boarding reference
    sold_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    idempotency_key  VARCHAR(128),
    request_hash     CHAR(64),                      -- canonical SHA-256 of the purchase payload
    UNIQUE (tenant_state_id, idempotency_key)       -- replay returns the original ticket
);

-- ---------------------------------------------------------------------------
-- Licensed dredgers + volumetric surveys (deduped)
-- ---------------------------------------------------------------------------
CREATE TABLE waterways.dredgers (
    dredger_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    vessel_name      VARCHAR(128) NOT NULL,
    license_no       VARCHAR(64) NOT NULL,
    operator_kyb_ref VARCHAR(64) NOT NULL,          -- mod-kyc-kyb business verification ref
    monthly_quota_m3 NUMERIC(14, 3) NOT NULL CHECK (monthly_quota_m3 > 0),
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, license_no)
);

CREATE TABLE waterways.surveys (
    survey_id        VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    dredger_id       VARCHAR(64) NOT NULL REFERENCES waterways.dredgers (dredger_id),
    polygon          GEOMETRY(Polygon, 4326) NOT NULL,  -- surveyed area ring
    volume_m3        NUMERIC(14, 3) NOT NULL CHECK (volume_m3 > 0),
    surveyed_at      TIMESTAMPTZ NOT NULL,
    verified_volume_m3 NUMERIC(14, 3),              -- Sedona volumetric cross-check
    month            CHAR(7) NOT NULL,              -- YYYY-MM rollup key
    dedupe_key       CHAR(64) NOT NULL,
    UNIQUE (tenant_state_id, dedupe_key)            -- prevents double royalty assessments
);

-- ---------------------------------------------------------------------------
-- Royalty assessments — hash-chained audit ledger (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE waterways.royalty_assessments (
    assessment_id    VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    dredger_id       VARCHAR(64) NOT NULL,
    survey_id        VARCHAR(64) NOT NULL REFERENCES waterways.surveys (survey_id),
    month            CHAR(7) NOT NULL,
    volume_m3        NUMERIC(14, 3) NOT NULL,
    royalty_kobo     BIGINT NOT NULL CHECK (royalty_kobo >= 0),
    over_quota       BOOLEAN NOT NULL DEFAULT FALSE,
    monthly_cumulative_m3 NUMERIC(14, 3) NOT NULL,
    monthly_quota_m3 NUMERIC(14, 3) NOT NULL,
    assessed_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_routes_tenant       ON waterways.routes (tenant_state_id);
CREATE INDEX idx_trips_tenant        ON waterways.trips (tenant_state_id, departure);
CREATE INDEX idx_tickets_trip        ON waterways.tickets (tenant_state_id, trip_id);
CREATE INDEX idx_dredgers_tenant     ON waterways.dredgers (tenant_state_id);
CREATE INDEX idx_surveys_dredger     ON waterways.surveys (tenant_state_id, dredger_id, month);
CREATE INDEX idx_surveys_polygon     ON waterways.surveys USING GIST (polygon);
CREATE INDEX idx_royalties_tenant    ON waterways.royalty_assessments (tenant_state_id, month);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE waterways.routes               ENABLE ROW LEVEL SECURITY;
ALTER TABLE waterways.trips                ENABLE ROW LEVEL SECURITY;
ALTER TABLE waterways.tickets              ENABLE ROW LEVEL SECURITY;
ALTER TABLE waterways.dredgers             ENABLE ROW LEVEL SECURITY;
ALTER TABLE waterways.surveys              ENABLE ROW LEVEL SECURITY;
ALTER TABLE waterways.royalty_assessments  ENABLE ROW LEVEL SECURITY;

CREATE POLICY routes_tenant_isolation ON waterways.routes
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY trips_tenant_isolation ON waterways.trips
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY tickets_tenant_isolation ON waterways.tickets
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY dredgers_tenant_isolation ON waterways.dredgers
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY surveys_tenant_isolation ON waterways.surveys
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY royalty_assessments_tenant_isolation ON waterways.royalty_assessments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
