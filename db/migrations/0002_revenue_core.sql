-- SOS Migration 0002 — Core Revenue (WP-05, EPIC-05)
-- STIN taxpayer registry + assessments. Balances live in TigerBeetle (ADR-002);
-- this schema stores assessment/billing state only, never mutable balances.

CREATE SCHEMA IF NOT EXISTS revenue;

CREATE TABLE revenue.taxpayers (
    taxpayer_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10)  NOT NULL,
    stin             VARCHAR(32)  UNIQUE NOT NULL,          -- e.g. NG-NAS-2026-892104
    nin_hash         VARCHAR(128),                          -- salted hash only; NDPA 2023
    bvn_hash         VARCHAR(128),
    cac_rc_number    VARCHAR(32),
    taxpayer_type    VARCHAR(16)  NOT NULL,                 -- INDIVIDUAL, BUSINESS, MDA
    display_name     VARCHAR(255) NOT NULL,
    lga_code         VARCHAR(20),
    status           VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE revenue.assessments (
    assessment_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10)  NOT NULL,
    taxpayer_stin    VARCHAR(32)  NOT NULL REFERENCES revenue.taxpayers(stin),
    mda_code         VARCHAR(32)  NOT NULL,
    revenue_head     VARCHAR(64)  NOT NULL,                 -- policy-pack governed
    tax_period_year  INTEGER      NOT NULL,
    gross_income_kobo      BIGINT NOT NULL CHECK (gross_income_kobo >= 0),
    allowable_deductions_kobo BIGINT NOT NULL DEFAULT 0 CHECK (allowable_deductions_kobo >= 0),
    calculated_tax_kobo    BIGINT NOT NULL CHECK (calculated_tax_kobo >= 0),
    bill_reference   VARCHAR(32)  UNIQUE NOT NULL,
    tigerbeetle_transfer_pending_id NUMERIC(39, 0),         -- 128-bit pending transfer ID
    status           VARCHAR(16)  NOT NULL DEFAULT 'ISSUED',-- ISSUED, PAID, DISPUTED, CANCELLED
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at          TIMESTAMPTZ
);

CREATE INDEX idx_assessments_tenant_period ON revenue.assessments (tenant_state_id, tax_period_year);
CREATE INDEX idx_assessments_stin          ON revenue.assessments (taxpayer_stin);

ALTER TABLE revenue.taxpayers   ENABLE ROW LEVEL SECURITY;
ALTER TABLE revenue.assessments ENABLE ROW LEVEL SECURITY;

CREATE POLICY taxpayers_state_isolation ON revenue.taxpayers
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY assessments_state_isolation ON revenue.assessments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
