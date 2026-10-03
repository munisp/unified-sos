-- SOS Migration 0011 — Mobility Switch domain (mod-mobility-switch)
-- PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- Fare clearing, operator settlement (TigerBeetle split legs), Mojaloop-style
-- pending-transfer escrows, NIBSS e-Bills notification dedupe, hash-chained
-- audit. All money in integer kobo; splits in basis points.

CREATE SCHEMA IF NOT EXISTS mobility;

-- ---------------------------------------------------------------------------
-- Fare tables (gazetted; union commission auto-split 3–8%)
-- ---------------------------------------------------------------------------
CREATE TABLE mobility.fare_tables (
    tenant_state_id  VARCHAR(10) PRIMARY KEY,
    gazette_reference VARCHAR(64) NOT NULL,         -- LAMATA harmonization gazette
    union_commission_pct NUMERIC(4, 2) NOT NULL CHECK (union_commission_pct BETWEEN 3.0 AND 8.0),
    fares            JSONB NOT NULL DEFAULT '[]',   -- [{mode, route, fare_kobo}] incl. "*" wildcard
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Tap/ticket clearing records awaiting settlement
-- ---------------------------------------------------------------------------
CREATE TABLE mobility.clearing_records (
    record_id        VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    operator_id      VARCHAR(64) NOT NULL,
    mode             VARCHAR(8) NOT NULL,           -- bus | rail | ferry
    route            VARCHAR(64) NOT NULL,
    fare_kobo        BIGINT NOT NULL CHECK (fare_kobo >= 0),
    card_ref         VARCHAR(64) NOT NULL,          -- Cowry-compatible card token (opaque)
    tapped_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settled_batch_id VARCHAR(64)
);

-- ---------------------------------------------------------------------------
-- Settlement batches with TigerBeetle split legs
-- ---------------------------------------------------------------------------
CREATE TABLE mobility.settlement_batches (
    batch_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    operator_id      VARCHAR(64) NOT NULL,
    record_count     INTEGER NOT NULL CHECK (record_count > 0),
    gross_kobo       BIGINT NOT NULL CHECK (gross_kobo > 0),
    legs             JSONB NOT NULL,                -- [{beneficiary, tigerbeetle_account_code, amount_kobo, transfer_code}]
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Pending-transfer escrows (FSPIOP prepare → fulfil/abort; idempotent on
-- transfer_id). PENDING rows past expires_at are auto-aborted.
-- ---------------------------------------------------------------------------
CREATE TABLE mobility.escrows (
    transfer_id      VARCHAR(64) PRIMARY KEY,
    batch_id         VARCHAR(64) NOT NULL,
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo > 0),
    condition        VARCHAR(128) NOT NULL,         -- FSPIOP condition
    state            VARCHAR(12) NOT NULL DEFAULT 'pending',  -- pending | posted | void
    fulfilment       VARCHAR(128),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       TIMESTAMPTZ NOT NULL,
    completed_at     TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- NIBSS e-Bills notifications — dedupe on bill_reference with payload hash
-- ---------------------------------------------------------------------------
CREATE TABLE mobility.bill_events (
    bill_reference   VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    request_hash     CHAR(64) NOT NULL,             -- canonical SHA-256 of the payload
    event            JSONB NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Append-only hash-chained audit
-- ---------------------------------------------------------------------------
CREATE TABLE mobility.audit_chain (
    event_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10),
    event_type       VARCHAR(64) NOT NULL,          -- settlement_executed | bill_event_recorded | ...
    payload          JSONB NOT NULL DEFAULT '{}',
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_clearing_operator_unsettled ON mobility.clearing_records (tenant_state_id, operator_id)
    WHERE settled_batch_id IS NULL;
CREATE INDEX idx_clearing_batch          ON mobility.clearing_records (settled_batch_id);
CREATE INDEX idx_batches_operator        ON mobility.settlement_batches (tenant_state_id, operator_id);
CREATE INDEX idx_escrows_batch           ON mobility.escrows (batch_id);
CREATE INDEX idx_escrows_expiry          ON mobility.escrows (expires_at) WHERE state = 'pending';
CREATE INDEX idx_bill_events_tenant      ON mobility.bill_events (tenant_state_id);
CREATE INDEX idx_audit_chain_recorded    ON mobility.audit_chain (recorded_at);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE mobility.fare_tables        ENABLE ROW LEVEL SECURITY;
ALTER TABLE mobility.clearing_records   ENABLE ROW LEVEL SECURITY;
ALTER TABLE mobility.settlement_batches ENABLE ROW LEVEL SECURITY;
-- escrows carry no tenant column in the reference model (isolated via their
-- parent settlement_batches row), so RLS is not enabled on them.
ALTER TABLE mobility.bill_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE mobility.audit_chain        ENABLE ROW LEVEL SECURITY;

CREATE POLICY fare_tables_tenant_isolation ON mobility.fare_tables
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY clearing_records_tenant_isolation ON mobility.clearing_records
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY settlement_batches_tenant_isolation ON mobility.settlement_batches
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY bill_events_tenant_isolation ON mobility.bill_events
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
-- escrows and audit_chain carry no tenant column of their own keyspace in the
-- reference store; enforce isolation where tenant_state_id is present.
CREATE POLICY audit_chain_tenant_isolation ON mobility.audit_chain
    FOR ALL USING (tenant_state_id IS NULL OR tenant_state_id = current_setting('app.current_state_tenant'));
