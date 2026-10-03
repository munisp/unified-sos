-- SOS Migration 0012 — Sector modules (ERP bridge, agri, border, market,
-- health, education, police CAD, safecity vision)
-- PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- All money integer kobo. Hash-chain audit tables use prev_hash/event_hash.

CREATE SCHEMA IF NOT EXISTS erp;
CREATE SCHEMA IF NOT EXISTS agri;
CREATE SCHEMA IF NOT EXISTS border;
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS health;
CREATE SCHEMA IF NOT EXISTS education;
CREATE SCHEMA IF NOT EXISTS police;
CREATE SCHEMA IF NOT EXISTS safecity;

-- ===========================================================================
-- ERP bridge (mod-erp-bridge): journal postings + dedupe
-- ===========================================================================
CREATE TABLE erp.journal_entries (
    entry_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    entry_date       DATE NOT NULL,
    memo             TEXT NOT NULL DEFAULT '',
    lines            JSONB NOT NULL DEFAULT '[]',   -- [{account_code, debit_kobo, credit_kobo}]
    source_event_id  VARCHAR(64) NOT NULL,
    hash             CHAR(64),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, source_event_id)       -- dedupe: one journal per source event
);

CREATE TABLE erp.outbound_records (
    event_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    entry_id         VARCHAR(64) NOT NULL REFERENCES erp.journal_entries (entry_id),
    source_event_id  VARCHAR(64) NOT NULL,
    entry_hash       CHAR(64) NOT NULL,
    backend          VARCHAR(24),                   -- ERPNEXT | ODOO | IFMIS
    external_ref     VARCHAR(128),
    status           VARCHAR(16) NOT NULL DEFAULT 'PENDING',  -- PENDING | POSTED | FAILED
    detail           TEXT NOT NULL DEFAULT '',
    posted_at        TIMESTAMPTZ,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL,
    "timestamp"      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ===========================================================================
-- Agri (mod-agri-trace / mod-agri-waybill): warehouse receipts + trace hops
-- ===========================================================================
CREATE TABLE agri.warehouse_receipts (
    receipt_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    warehouse_id     VARCHAR(64) NOT NULL,
    lot_id           VARCHAR(64),                   -- mod-agri-trace lot
    waybill_number   VARCHAR(64),                   -- mod-agri-waybill linkage
    holder_id        VARCHAR(64) NOT NULL,
    depositor_id     VARCHAR(64),
    produce_type     VARCHAR(40),
    quantity_kg      NUMERIC(14, 3) NOT NULL CHECK (quantity_kg > 0),
    grade            VARCHAR(8),                    -- A | B | C
    storage_fees_kobo BIGINT NOT NULL DEFAULT 0,
    storage_location VARCHAR(128),
    collateralized   BOOLEAN NOT NULL DEFAULT FALSE,
    status           VARCHAR(16) NOT NULL DEFAULT 'ISSUED',  -- ISSUED | PLEDGED | REDEEMED
    pledgee_ref      VARCHAR(128),
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE agri.trace_hops (
    hop_id           VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    lot_id           VARCHAR(64) NOT NULL,
    stage            VARCHAR(24) NOT NULL,          -- FARM | AGGREGATION | WAREHOUSE | ...
    actor            VARCHAR(128) NOT NULL,
    latitude         NUMERIC(9, 6),
    longitude        NUMERIC(9, 6),
    note             TEXT NOT NULL DEFAULT '',
    occurred_at      TIMESTAMPTZ NOT NULL,
    prev_hash        CHAR(64) NOT NULL,             -- per-lot hash chain
    event_hash       CHAR(64) NOT NULL
);

-- ===========================================================================
-- Border transit (mod-border-transit): crossings, consignments, levies
-- ===========================================================================
CREATE TABLE border.crossings (
    crossing_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    name             VARCHAR(128) NOT NULL,
    neighbor_country VARCHAR(64) NOT NULL,
    latitude         NUMERIC(9, 6),
    longitude        NUMERIC(9, 6)
);

CREATE TABLE border.consignments (
    consignment_id   VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    trader_ref       VARCHAR(128) NOT NULL,
    rfid_tag_id      VARCHAR(64) NOT NULL,
    goods_description TEXT NOT NULL,
    hs_code          VARCHAR(16) NOT NULL,
    declared_value_kobo BIGINT NOT NULL CHECK (declared_value_kobo >= 0),
    origin_crossing_id VARCHAR(64) NOT NULL REFERENCES border.crossings (crossing_id),
    destination_crossing_id VARCHAR(64) NOT NULL REFERENCES border.crossings (crossing_id),
    corridor         TEXT[] NOT NULL DEFAULT '{}',
    state            VARCHAR(24) NOT NULL DEFAULT 'DECLARED',
    seal_intact      BOOLEAN NOT NULL DEFAULT TRUE,
    declared_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE border.levy_assessments (
    assessment_id    VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    consignment_id   VARCHAR(64) NOT NULL REFERENCES border.consignments (consignment_id),
    flat_fee_kobo    BIGINT NOT NULL CHECK (flat_fee_kobo >= 0),
    ad_valorem_bps   INTEGER NOT NULL CHECK (ad_valorem_bps >= 0),
    ad_valorem_kobo  BIGINT NOT NULL CHECK (ad_valorem_kobo >= 0),
    total_kobo       BIGINT NOT NULL CHECK (total_kobo >= 0),
    ledger_entries   JSONB NOT NULL DEFAULT '[]',   -- double-entry legs
    assessed_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, consignment_id)        -- one assessment per consignment
);

CREATE TABLE border.clearance_audit (
    event_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    consignment_id   VARCHAR(64) NOT NULL,
    decision         VARCHAR(16) NOT NULL,          -- CLEARED | REJECTED
    officer_ref      VARCHAR(128) NOT NULL,
    decided_at       TIMESTAMPTZ NOT NULL,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL
);

-- ===========================================================================
-- Market (mod-market): stalls + stallage tickets (edge-ingest dedupe)
-- ===========================================================================
CREATE TABLE market.markets (
    market_id        VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    name             VARCHAR(128) NOT NULL,
    market_type      VARCHAR(24) NOT NULL,
    lga              VARCHAR(64) NOT NULL,
    stall_capacity   INTEGER NOT NULL DEFAULT 0,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE market.stalls (
    stall_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    market_id        VARCHAR(64) NOT NULL REFERENCES market.markets (market_id),
    block            VARCHAR(16) NOT NULL,
    number           VARCHAR(16) NOT NULL,
    daily_fee_kobo   BIGINT NOT NULL CHECK (daily_fee_kobo >= 0),
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (market_id, block, number)
);

CREATE TABLE market.stallage_tickets (
    ticket_id        VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    market_id        VARCHAR(64) NOT NULL,
    stall_id         VARCHAR(64) NOT NULL,
    trader_id        VARCHAR(64),
    service_date     DATE NOT NULL,
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo >= 0),
    transfer_code    INTEGER NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'ISSUED',  -- ISSUED | PAID | DISPUTED | VOID
    origin           VARCHAR(16) NOT NULL DEFAULT 'ONLINE',  -- ONLINE | OFFLINE_EDGE
    edge_device_id   VARCHAR(64),
    edge_sequence    INTEGER,
    edge_signature   VARCHAR(128),
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (edge_device_id, edge_sequence)          -- edge replay dedupe
);

-- ===========================================================================
-- Health (mod-health): billing accounts, invoices, payer claims
-- ===========================================================================
CREATE TABLE health.billing_accounts (
    account_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    facility_id      VARCHAR(64) NOT NULL,
    patient_ref      VARCHAR(128) NOT NULL,         -- opaque reference, no PII
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE health.invoices (
    invoice_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    account_id       VARCHAR(64) NOT NULL REFERENCES health.billing_accounts (account_id),
    facility_id      VARCHAR(64) NOT NULL,
    lines            JSONB NOT NULL DEFAULT '[]',   -- [{service_code, description, quantity, unit_amount_kobo}]
    status           VARCHAR(16) NOT NULL DEFAULT 'ISSUED',  -- ISSUED | PAID | VOID
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at          TIMESTAMPTZ,
    ledger_transfer_code INTEGER NOT NULL
);

CREATE TABLE health.claims (
    claim_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    invoice_id       VARCHAR(64) NOT NULL REFERENCES health.invoices (invoice_id),
    payer            VARCHAR(64) NOT NULL,          -- insurer/HMO reference
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo >= 0),
    status           VARCHAR(16) NOT NULL DEFAULT 'SUBMITTED',  -- SUBMITTED | APPROVED | REJECTED | PAID
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    adjudicated_at   TIMESTAMPTZ,
    adjudication_note TEXT
);

-- ===========================================================================
-- Education (mod-education): students, fee invoices, course registration
-- ===========================================================================
CREATE TABLE education.students (
    student_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    institution_id   VARCHAR(64) NOT NULL,
    matric_no        VARCHAR(32) NOT NULL,
    enrolled_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (institution_id, matric_no)
);

CREATE TABLE education.student_invoices (
    invoice_id       VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    student_id       VARCHAR(64) NOT NULL REFERENCES education.students (student_id),
    session          VARCHAR(16) NOT NULL,          -- e.g. 2024/2025
    lines            JSONB NOT NULL DEFAULT '[]',   -- [{fee_type, amount_kobo}]
    status           VARCHAR(16) NOT NULL DEFAULT 'ISSUED',  -- ISSUED | PAID | VOID
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at          TIMESTAMPTZ,
    ledger_transfer_code INTEGER NOT NULL
);

CREATE TABLE education.registrations (
    registration_id  VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    student_id       VARCHAR(64) NOT NULL REFERENCES education.students (student_id),
    session          VARCHAR(16) NOT NULL,
    courses          TEXT[] NOT NULL DEFAULT '{}',
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, session)
);

-- ===========================================================================
-- Police CAD (mod-police-cad): incidents, units, donations/disbursements
-- ===========================================================================
CREATE TABLE police.incidents (
    incident_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    agency           VARCHAR(24) NOT NULL,
    category         VARCHAR(32) NOT NULL,
    latitude         NUMERIC(9, 6) NOT NULL,
    longitude        NUMERIC(9, 6) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'OPEN',  -- OPEN | DISPATCHED | CLOSED
    reported_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE police.units (
    unit_id          VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    agency           VARCHAR(24) NOT NULL,
    call_sign        VARCHAR(32) NOT NULL,
    personnel_count  INTEGER NOT NULL DEFAULT 0,
    biometric_enrolled BOOLEAN NOT NULL DEFAULT FALSE,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE police.donations (
    donation_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    donor_ref        VARCHAR(128) NOT NULL,         -- hashed donor reference
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo > 0),
    received_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE police.disbursements (
    disbursement_id  VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    purpose          VARCHAR(128) NOT NULL,
    amount_kobo      BIGINT NOT NULL CHECK (amount_kobo > 0),
    disbursed_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ===========================================================================
-- Safecity vision (mod-safecity-vision): face enrolments, match events,
-- crowd/anomaly audit. NDPA: embeddings as integer vectors, retention-bounded.
-- ===========================================================================
CREATE TABLE safecity.face_enrolments (
    enrolment_id     VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    subject_ref      VARCHAR(128) NOT NULL,         -- opaque subject reference
    embedding        INTEGER[] NOT NULL,            -- quantized embedding vector
    authorization_ref VARCHAR(64) NOT NULL,         -- lawful-basis reference
    enrolled_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retention_until  TIMESTAMPTZ                    -- auto-purge deadline (NDPA)
);

CREATE TABLE safecity.face_matches (
    match_event_id   VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    camera_id        VARCHAR(64),
    matched          BOOLEAN NOT NULL,
    subject_ref      VARCHAR(128),
    similarity       NUMERIC(5, 4) NOT NULL CHECK (similarity BETWEEN 0 AND 1),
    threshold        NUMERIC(5, 4) NOT NULL CHECK (threshold BETWEEN 0 AND 1),
    matched_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE safecity.audit_chain (
    event_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    event_type       VARCHAR(32) NOT NULL,          -- FACE_MATCH | CROWD_ALERT | ANOMALY
    camera_id        VARCHAR(64),
    payload          JSONB NOT NULL DEFAULT '{}',
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL
);

-- ===========================================================================
-- Indexes
-- ===========================================================================
CREATE INDEX idx_erp_entries_tenant        ON erp.journal_entries (tenant_state_id, entry_date);
CREATE INDEX idx_erp_outbound_entry        ON erp.outbound_records (tenant_state_id, entry_id);
CREATE INDEX idx_agri_receipts_holder      ON agri.warehouse_receipts (tenant_state_id, holder_id);
CREATE INDEX idx_agri_receipts_waybill     ON agri.warehouse_receipts (tenant_state_id, waybill_number);
CREATE INDEX idx_agri_hops_lot             ON agri.trace_hops (tenant_state_id, lot_id, occurred_at);
CREATE INDEX idx_border_consignments_tag   ON border.consignments (tenant_state_id, rfid_tag_id);
CREATE INDEX idx_border_levies_tenant      ON border.levy_assessments (tenant_state_id, assessed_at);
CREATE INDEX idx_market_stalls_market      ON market.stalls (tenant_state_id, market_id);
CREATE INDEX idx_market_tickets_date       ON market.stallage_tickets (tenant_state_id, service_date);
CREATE INDEX idx_health_invoices_account   ON health.invoices (tenant_state_id, account_id);
CREATE INDEX idx_health_claims_invoice     ON health.claims (tenant_state_id, invoice_id);
CREATE INDEX idx_edu_invoices_student      ON education.student_invoices (tenant_state_id, student_id);
CREATE INDEX idx_police_incidents_status   ON police.incidents (tenant_state_id, status);
CREATE INDEX idx_safecity_enrol_subject    ON safecity.face_enrolments (tenant_state_id, subject_ref);
CREATE INDEX idx_safecity_matches_camera   ON safecity.face_matches (tenant_state_id, camera_id, matched_at);

-- ===========================================================================
-- Tenant isolation via Row-Level Security
-- ===========================================================================
ALTER TABLE erp.journal_entries          ENABLE ROW LEVEL SECURITY;
ALTER TABLE erp.outbound_records         ENABLE ROW LEVEL SECURITY;
ALTER TABLE agri.warehouse_receipts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE agri.trace_hops              ENABLE ROW LEVEL SECURITY;
ALTER TABLE border.crossings             ENABLE ROW LEVEL SECURITY;
ALTER TABLE border.consignments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE border.levy_assessments      ENABLE ROW LEVEL SECURITY;
ALTER TABLE border.clearance_audit       ENABLE ROW LEVEL SECURITY;
ALTER TABLE market.markets               ENABLE ROW LEVEL SECURITY;
ALTER TABLE market.stalls                ENABLE ROW LEVEL SECURITY;
ALTER TABLE market.stallage_tickets      ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.billing_accounts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.invoices              ENABLE ROW LEVEL SECURITY;
ALTER TABLE health.claims                ENABLE ROW LEVEL SECURITY;
ALTER TABLE education.students           ENABLE ROW LEVEL SECURITY;
ALTER TABLE education.student_invoices   ENABLE ROW LEVEL SECURITY;
ALTER TABLE education.registrations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE police.incidents             ENABLE ROW LEVEL SECURITY;
ALTER TABLE police.units                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE police.donations             ENABLE ROW LEVEL SECURITY;
ALTER TABLE police.disbursements         ENABLE ROW LEVEL SECURITY;
ALTER TABLE safecity.face_enrolments     ENABLE ROW LEVEL SECURITY;
ALTER TABLE safecity.face_matches        ENABLE ROW LEVEL SECURITY;
ALTER TABLE safecity.audit_chain         ENABLE ROW LEVEL SECURITY;

CREATE POLICY erp_entries_tenant_isolation ON erp.journal_entries
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY erp_outbound_tenant_isolation ON erp.outbound_records
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY agri_receipts_tenant_isolation ON agri.warehouse_receipts
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY agri_hops_tenant_isolation ON agri.trace_hops
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY border_crossings_tenant_isolation ON border.crossings
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY border_consignments_tenant_isolation ON border.consignments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY border_levies_tenant_isolation ON border.levy_assessments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY border_clearance_tenant_isolation ON border.clearance_audit
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY market_markets_tenant_isolation ON market.markets
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY market_stalls_tenant_isolation ON market.stalls
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY market_tickets_tenant_isolation ON market.stallage_tickets
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY health_accounts_tenant_isolation ON health.billing_accounts
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY health_invoices_tenant_isolation ON health.invoices
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY health_claims_tenant_isolation ON health.claims
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY edu_students_tenant_isolation ON education.students
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY edu_invoices_tenant_isolation ON education.student_invoices
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY edu_registrations_tenant_isolation ON education.registrations
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY police_incidents_tenant_isolation ON police.incidents
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY police_units_tenant_isolation ON police.units
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY police_donations_tenant_isolation ON police.donations
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY police_disbursements_tenant_isolation ON police.disbursements
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY safecity_enrolments_tenant_isolation ON safecity.face_enrolments
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY safecity_matches_tenant_isolation ON safecity.face_matches
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY safecity_audit_tenant_isolation ON safecity.audit_chain
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
