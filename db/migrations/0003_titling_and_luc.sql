-- SOS Migration 0003 — e-C-of-O titling workflow state & Land Use Charge billing
-- WP-06 / EPIC-06 · PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- Complements 0001_cadastre.sql (parcels) and 0002_revenue_core.sql.
-- Durable titling workflow history is held by Temporal in production
-- (ADR-005); these tables are the queryable projection for APIs/audit.

CREATE SCHEMA IF NOT EXISTS cadastre;
CREATE SCHEMA IF NOT EXISTS luc;

-- ---------------------------------------------------------------------------
-- e-C-of-O titling workflow projection + signed digital titles (mod-gis-lands)
-- ---------------------------------------------------------------------------
CREATE TABLE cadastre.titling_workflows (
    workflow_id      VARCHAR(80) PRIMARY KEY,        -- Temporal workflow ID
    tenant_state_id  VARCHAR(10) NOT NULL,
    parcel_id        UUID NOT NULL REFERENCES cadastre.parcels (parcel_id),
    stage            VARCHAR(32) NOT NULL,           -- SURVEYOR_VALIDATION ... ISSUANCE
    status           VARCHAR(16) NOT NULL DEFAULT 'RUNNING',  -- RUNNING | ISSUED | REJECTED
    rejection_reason TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE cadastre.titling_transitions (
    transition_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id      VARCHAR(80) NOT NULL REFERENCES cadastre.titling_workflows (workflow_id),
    tenant_state_id  VARCHAR(10) NOT NULL,
    stage            VARCHAR(32) NOT NULL,
    actor            VARCHAR(128) NOT NULL,
    approved         BOOLEAN NOT NULL,
    note             TEXT,
    entered_at       TIMESTAMPTZ NOT NULL,
    exited_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE cadastre.digital_titles (
    c_of_o_number         VARCHAR(64) PRIMARY KEY,   -- e.g. OGUN/COFO/2026/000001
    tenant_state_id       VARCHAR(10) NOT NULL,
    parcel_id             UUID NOT NULL REFERENCES cadastre.parcels (parcel_id),
    workflow_id           VARCHAR(80) NOT NULL REFERENCES cadastre.titling_workflows (workflow_id),
    signed_title_jws      TEXT NOT NULL,             -- Ed25519 (EdDSA) compact JWS
    governor_consent_jws  TEXT NOT NULL,             -- governor countersignature JWS
    registry_key_kid      VARCHAR(64) NOT NULL,      -- key version (OpenBao/Vault)
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Land Use Charge valuation runs & bills (mod-gis-luc; kobo minor units)
-- ---------------------------------------------------------------------------
CREATE TABLE luc.valuation_runs (
    valuation_run_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id                 VARCHAR(10) NOT NULL,
    assessment_year                 INTEGER NOT NULL,
    parcels_assessed                INTEGER NOT NULL,
    bills_generated                 INTEGER NOT NULL,
    findings_ingested               INTEGER NOT NULL,
    compliant_findings              INTEGER NOT NULL,
    unassessed_improvement_bills    INTEGER NOT NULL,
    encroachment_provisional_bills  INTEGER NOT NULL,
    total_billed_kobo               BIGINT NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE luc.bills (
    bill_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    valuation_run_id       UUID NOT NULL REFERENCES luc.valuation_runs (valuation_run_id),
    tenant_state_id        VARCHAR(10) NOT NULL,
    parcel_uin             VARCHAR(64),              -- NULL for pure encroachment bills
    building_footprint_id  VARCHAR(64),
    owner_stin             VARCHAR(32),
    land_use_type          VARCHAR(32) NOT NULL,
    charge_area_sqm        NUMERIC(14, 4) NOT NULL,
    rate_kobo_per_sqm      INTEGER NOT NULL,
    gross_amount_kobo      BIGINT NOT NULL,
    relief_fraction        NUMERIC(4, 3) NOT NULL DEFAULT 0,
    net_amount_kobo        BIGINT NOT NULL,
    audit_status           VARCHAR(32) NOT NULL,     -- COMPLIANT | UNASSESSED_IMPROVEMENT | UNREGISTERED_ENCROACHMENT
    status                 VARCHAR(16) NOT NULL DEFAULT 'ISSUED',  -- ISSUED | PROVISIONAL | VOID
    assessment_year        INTEGER NOT NULL,
    issued_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_luc_bills_tenant_parcel ON luc.bills (tenant_state_id, parcel_uin);
CREATE INDEX idx_luc_runs_tenant         ON luc.valuation_runs (tenant_state_id);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security (same session variable as 0001)
-- ---------------------------------------------------------------------------
ALTER TABLE cadastre.titling_workflows   ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadastre.titling_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadastre.digital_titles      ENABLE ROW LEVEL SECURITY;
ALTER TABLE luc.valuation_runs           ENABLE ROW LEVEL SECURITY;
ALTER TABLE luc.bills                    ENABLE ROW LEVEL SECURITY;

CREATE POLICY titling_workflows_tenant_isolation ON cadastre.titling_workflows
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY titling_transitions_tenant_isolation ON cadastre.titling_transitions
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY digital_titles_tenant_isolation ON cadastre.digital_titles
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY luc_runs_tenant_isolation ON luc.valuation_runs
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY luc_bills_tenant_isolation ON luc.bills
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
