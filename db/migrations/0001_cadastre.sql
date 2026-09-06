-- SOS Migration 0001 — Cadastral Land Registry (WP-06, EPIC-06)
-- PostgreSQL 16+ / PostGIS 3.4+ · Tenant-isolated via Row-Level Security

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS cadastre;

CREATE TABLE cadastre.parcels (
    parcel_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10)  NOT NULL,
    lga_id           VARCHAR(20)  NOT NULL,
    parcel_uin       VARCHAR(64)  UNIQUE NOT NULL,          -- Unique Identification Number
    owner_stin       VARCHAR(32)  NOT NULL,
    land_use_type    VARCHAR(32)  NOT NULL,                 -- RESIDENTIAL, COMMERCIAL, INDUSTRIAL, AGRI
    survey_plan_no   VARCHAR(64)  NOT NULL,
    beacon_count     INTEGER      NOT NULL,
    area_sqm         NUMERIC(14, 4) NOT NULL,
    title_type       VARCHAR(20)  NOT NULL DEFAULT 'UNREGISTERED', -- C_OF_O, GOV_CONSENT, R_OF_O
    c_of_o_number    VARCHAR(64),
    status           VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE',
    boundary_geom    GEOMETRY(Polygon, 4326) NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_parcels_spatial    ON cadastre.parcels USING GIST (boundary_geom);
CREATE INDEX idx_parcels_tenant_lga ON cadastre.parcels (tenant_state_id, lga_id);

-- Enforce tenancy isolation with Row-Level Security
ALTER TABLE cadastre.parcels ENABLE ROW LEVEL SECURITY;
CREATE POLICY parcel_state_isolation_policy ON cadastre.parcels
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));

-- Topological integrity: reject overlapping new registrations (acceptance M4.2:
-- zero overlapping-polygon tolerance, sub-meter precision)
CREATE OR REPLACE FUNCTION cadastre.reject_overlapping_parcel() RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM cadastre.parcels p
        WHERE p.tenant_state_id = NEW.tenant_state_id
          AND p.status = 'ACTIVE'
          AND ST_Intersects(p.boundary_geom, NEW.boundary_geom)
          AND NOT ST_Touches(p.boundary_geom, NEW.boundary_geom)
    ) THEN
        RAISE EXCEPTION 'Parcel boundary overlaps an existing active title in state %', NEW.tenant_state_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_parcels_no_overlap
    BEFORE INSERT ON cadastre.parcels
    FOR EACH ROW EXECUTE FUNCTION cadastre.reject_overlapping_parcel();
