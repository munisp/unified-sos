-- SOS Migration 0007 — Identity & Data domain (mod-identity)
-- PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- Complements 0001–0006.
-- NDPA data-minimization: raw NIN is NEVER stored; only nin_hash (SHA-256)
-- persists. Every access lands in the hash-chained identity.audit_entries
-- (entry_hash = SHA-256(prev_hash || '|' || canonical payload)).

CREATE SCHEMA IF NOT EXISTS identity;

-- ---------------------------------------------------------------------------
-- Residents (LASRRA-style registry), credentials, guardianship
-- ---------------------------------------------------------------------------
CREATE TABLE identity.residents (
    resident_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,          -- lagos | ogun | osun | benue | nasarawa | taraba
    nin_hash         CHAR(64) NOT NULL,             -- SHA-256 of NIN; raw NIN never stored
    nin_tail         CHAR(3) NOT NULL,              -- last 3 chars only (masked display)
    full_name        VARCHAR(256) NOT NULL,
    address          TEXT NOT NULL,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    status           VARCHAR(12) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | DECEASED | SUSPENDED
    date_of_birth    DATE,
    UNIQUE (tenant_state_id, nin_hash)
);

CREATE TABLE identity.credentials (
    credential_id    VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    resident_id      VARCHAR(64) NOT NULL REFERENCES identity.residents (resident_id),
    credential_type  VARCHAR(40) NOT NULL,          -- RESIDENCY_CARD | LASRRA_ID | ...
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at       TIMESTAMPTZ
);

CREATE TABLE identity.guardian_links (
    link_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id      VARCHAR(10) NOT NULL,
    resident_id          VARCHAR(64) NOT NULL REFERENCES identity.residents (resident_id),
    guardian_resident_id VARCHAR(64) NOT NULL REFERENCES identity.residents (resident_id),
    kyc_case_ref         VARCHAR(64) NOT NULL,      -- verified mod-kyc-kyb case
    expires_at           TIMESTAMPTZ NOT NULL,      -- auto-expires at 18th birthday
    created_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Metered verification API: consumers, consent, usage, settlements
-- ---------------------------------------------------------------------------
CREATE TABLE identity.api_consumers (
    consumer_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    name             VARCHAR(256) NOT NULL,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE identity.consent_grants (
    grant_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    resident_id      VARCHAR(64) NOT NULL REFERENCES identity.residents (resident_id),
    consumer_id      VARCHAR(64) NOT NULL REFERENCES identity.api_consumers (consumer_id),
    purpose          VARCHAR(32) NOT NULL,          -- ADDRESS_VERIFICATION | RESIDENCY_ATTESTATION | KYC_ADJUNCT
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ
);

CREATE TABLE identity.usage_records (
    usage_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    consumer_id      VARCHAR(64) NOT NULL REFERENCES identity.api_consumers (consumer_id),
    product          VARCHAR(32) NOT NULL,
    fee_kobo         BIGINT NOT NULL CHECK (fee_kobo >= 0),
    result_id        VARCHAR(64) NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settlement_id    VARCHAR(64)
);

CREATE TABLE identity.settlement_records (
    settlement_id    VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    consumer_id      VARCHAR(64) NOT NULL REFERENCES identity.api_consumers (consumer_id),
    usage_ids        TEXT[] NOT NULL DEFAULT '{}',
    total_kobo       BIGINT NOT NULL CHECK (total_kobo >= 0),
    lines            JSONB NOT NULL DEFAULT '[]',   -- SettlementLine legs (accounts 3001/2099)
    settled_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Append-only hash-chained audit (mirrors kyc_kyb.audit_entries, 0005)
-- ---------------------------------------------------------------------------
CREATE TABLE identity.audit_entries (
    entry_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    seq              BIGINT NOT NULL,
    action           VARCHAR(64) NOT NULL,          -- VERIFY_GRANTED | VERIFY_DENIED_NO_CONSENT | ...
    actor_id         VARCHAR(128) NOT NULL,
    subject_id       VARCHAR(128) NOT NULL,
    details          TEXT NOT NULL DEFAULT '',
    prev_hash        CHAR(64) NOT NULL,             -- hash chain: SHA-256(prev_hash || '|' || payload)
    entry_hash       CHAR(64) NOT NULL,
    at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, seq)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_residents_tenant_status   ON identity.residents (tenant_state_id, status);
CREATE INDEX idx_residents_nin_hash        ON identity.residents (tenant_state_id, nin_hash);
CREATE INDEX idx_credentials_resident      ON identity.credentials (tenant_state_id, resident_id);
CREATE INDEX idx_guardian_links_resident   ON identity.guardian_links (tenant_state_id, resident_id);
CREATE INDEX idx_consumers_tenant          ON identity.api_consumers (tenant_state_id);
CREATE INDEX idx_consent_lookup            ON identity.consent_grants (tenant_state_id, resident_id, consumer_id, purpose);
CREATE INDEX idx_usage_consumer            ON identity.usage_records (tenant_state_id, consumer_id);
CREATE INDEX idx_audit_entries_seq         ON identity.audit_entries (tenant_state_id, seq);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security (same session variable as 0001-0006)
-- ---------------------------------------------------------------------------
ALTER TABLE identity.residents           ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.credentials         ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.guardian_links      ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.api_consumers       ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.consent_grants      ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.usage_records       ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.settlement_records  ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.audit_entries       ENABLE ROW LEVEL SECURITY;

CREATE POLICY residents_tenant_isolation ON identity.residents
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY credentials_tenant_isolation ON identity.credentials
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY guardian_links_tenant_isolation ON identity.guardian_links
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY api_consumers_tenant_isolation ON identity.api_consumers
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY consent_grants_tenant_isolation ON identity.consent_grants
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY usage_records_tenant_isolation ON identity.usage_records
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY settlement_records_tenant_isolation ON identity.settlement_records
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY audit_entries_tenant_isolation ON identity.audit_entries
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
