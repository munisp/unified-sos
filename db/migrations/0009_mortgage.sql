-- SOS Migration 0009 — Mortgage & Charges domain (mod-mortgage)
-- PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- All monetary amounts are integer kobo; interest rates in basis points.
-- Payments are idempotent on (tenant, idempotency_key).

CREATE SCHEMA IF NOT EXISTS mortgage;

-- ---------------------------------------------------------------------------
-- Mortgages (lifecycle incl. foreclosure columns)
-- ---------------------------------------------------------------------------
CREATE TABLE mortgage.mortgages (
    mortgage_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    applicant_id     VARCHAR(64) NOT NULL,
    parcel_id        VARCHAR(64) NOT NULL,          -- cadastre.parcels linkage (0001)
    title_ref        VARCHAR(64) NOT NULL,
    principal_kobo   BIGINT NOT NULL CHECK (principal_kobo >= 0),
    rate_bps         INTEGER NOT NULL CHECK (rate_bps >= 0),
    term_months      INTEGER NOT NULL CHECK (term_months > 0),
    status           VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING | APPROVED | DISBURSED | DEFAULTED | FORECLOSED | DISCHARGED | CLOSED
    credit_score     INTEGER,
    approved_by      VARCHAR(128),
    approval_reason  TEXT,
    lien_id          VARCHAR(64),
    outstanding_principal_kobo BIGINT NOT NULL DEFAULT 0 CHECK (outstanding_principal_kobo >= 0),
    disbursement_transfer_id VARCHAR(64),
    disbursed_at     TIMESTAMPTZ,
    discharged_at    TIMESTAMPTZ,
    defaulted_at     TIMESTAMPTZ,
    foreclosed_at    TIMESTAMPTZ,
    foreclosure_reason TEXT,
    credit_balance_kobo BIGINT NOT NULL DEFAULT 0,
    credit_refund_transfer_id VARCHAR(64),
    closed_at        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Amortization schedule + payments
-- ---------------------------------------------------------------------------
CREATE TABLE mortgage.installments (
    mortgage_id      VARCHAR(64) NOT NULL REFERENCES mortgage.mortgages (mortgage_id),
    tenant_state_id  VARCHAR(10) NOT NULL,
    seq              INTEGER NOT NULL,
    due_date         DATE NOT NULL,
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo >= 0),
    interest_kobo    BIGINT NOT NULL CHECK (interest_kobo >= 0),
    principal_kobo   BIGINT NOT NULL CHECK (principal_kobo >= 0),
    paid_interest_kobo BIGINT NOT NULL DEFAULT 0 CHECK (paid_interest_kobo >= 0),
    paid_principal_kobo BIGINT NOT NULL DEFAULT 0 CHECK (paid_principal_kobo >= 0),
    PRIMARY KEY (mortgage_id, seq)
);

CREATE TABLE mortgage.payments (
    payment_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    mortgage_id      VARCHAR(64) NOT NULL REFERENCES mortgage.mortgages (mortgage_id),
    idempotency_key  VARCHAR(128) NOT NULL,
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo >= 0),
    interest_kobo    BIGINT NOT NULL DEFAULT 0 CHECK (interest_kobo >= 0),
    principal_kobo   BIGINT NOT NULL DEFAULT 0 CHECK (principal_kobo >= 0),
    overpayment_kobo BIGINT NOT NULL DEFAULT 0 CHECK (overpayment_kobo >= 0),
    transfer_id      VARCHAR(64) NOT NULL,          -- TigerBeetle transfer reference
    applied_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, idempotency_key)       -- payment replay protection
);

-- ---------------------------------------------------------------------------
-- Liens / charges on titles (priority-ordered, second charges supported)
-- ---------------------------------------------------------------------------
CREATE TABLE mortgage.liens (
    lien_id          VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    mortgage_id      VARCHAR(64) NOT NULL REFERENCES mortgage.mortgages (mortgage_id),
    parcel_id        VARCHAR(64) NOT NULL,
    title_ref        VARCHAR(64) NOT NULL,
    priority         INTEGER NOT NULL DEFAULT 1,
    second_charge    BOOLEAN NOT NULL DEFAULT FALSE,
    senior_lien_id   VARCHAR(64),
    status           VARCHAR(16) NOT NULL DEFAULT 'REGISTERED',  -- REGISTERED | RELEASED
    title_hash       CHAR(64),                      -- SHA-256 of caveat lodged on title
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    released_at      TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Foreclosure: valuation, lawful sale, proceeds distribution
-- ---------------------------------------------------------------------------
CREATE TABLE mortgage.foreclosure_sales (
    mortgage_id      VARCHAR(64) PRIMARY KEY REFERENCES mortgage.mortgages (mortgage_id),
    tenant_state_id  VARCHAR(10) NOT NULL,
    possession_reference VARCHAR(64),
    possession_registered_at TIMESTAMPTZ,
    valuation_kobo   BIGINT CHECK (valuation_kobo >= 0),
    reserve_price_kobo BIGINT CHECK (reserve_price_kobo >= 0),
    valuer_id        VARCHAR(128),
    sale_authorized_at TIMESTAMPTZ,
    purchaser_id     VARCHAR(64),
    gross_proceeds_kobo BIGINT CHECK (gross_proceeds_kobo >= 0),
    sale_costs_kobo  BIGINT CHECK (sale_costs_kobo >= 0),
    title_transfer_ref VARCHAR(64),
    sale_transfer_id VARCHAR(64),
    sold_at          TIMESTAMPTZ,
    distribution     JSONB,                         -- waterfall legs (integer kobo)
    deficiency_kobo  BIGINT NOT NULL DEFAULT 0,
    proceeds_distributed_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Append-only hash-chained audit
-- ---------------------------------------------------------------------------
CREATE TABLE mortgage.audit_entries (
    entry_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    sequence         BIGINT NOT NULL,
    mortgage_id      VARCHAR(64),
    action           VARCHAR(64) NOT NULL,
    actor            VARCHAR(128) NOT NULL,
    payload_hash     CHAR(64) NOT NULL,
    prev_hash        CHAR(64) NOT NULL,             -- hash chain: SHA-256(prev_hash || payload_hash)
    entry_hash       CHAR(64) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, sequence)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_mortgages_tenant_status ON mortgage.mortgages (tenant_state_id, status);
CREATE INDEX idx_mortgages_parcel        ON mortgage.mortgages (tenant_state_id, parcel_id);
CREATE INDEX idx_installments_due        ON mortgage.installments (tenant_state_id, due_date);
CREATE INDEX idx_payments_mortgage       ON mortgage.payments (tenant_state_id, mortgage_id);
CREATE INDEX idx_liens_parcel            ON mortgage.liens (tenant_state_id, parcel_id, priority);
CREATE INDEX idx_liens_mortgage          ON mortgage.liens (tenant_state_id, mortgage_id);
CREATE INDEX idx_foreclosure_tenant      ON mortgage.foreclosure_sales (tenant_state_id);
CREATE INDEX idx_mortgage_audit          ON mortgage.audit_entries (tenant_state_id, mortgage_id, sequence);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE mortgage.mortgages         ENABLE ROW LEVEL SECURITY;
ALTER TABLE mortgage.installments      ENABLE ROW LEVEL SECURITY;
ALTER TABLE mortgage.payments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE mortgage.liens             ENABLE ROW LEVEL SECURITY;
ALTER TABLE mortgage.foreclosure_sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE mortgage.audit_entries     ENABLE ROW LEVEL SECURITY;

CREATE POLICY mortgages_tenant_isolation ON mortgage.mortgages
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY installments_tenant_isolation ON mortgage.installments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY payments_tenant_isolation ON mortgage.payments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY liens_tenant_isolation ON mortgage.liens
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY foreclosure_sales_tenant_isolation ON mortgage.foreclosure_sales
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY audit_entries_tenant_isolation ON mortgage.audit_entries
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
