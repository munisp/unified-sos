-- SOS Migration 0004 — ENV-09 environment/carbon domain & CIT-11 citizen domain
-- v3.0 · PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- Complements 0001–0003. Monetary columns are BIGINT kobo; balances are never
-- mutated here — the TigerBeetle ledger is the balance source of truth.
-- Durable workflow history (EIA, payroll verification) is held by Temporal in
-- production (ADR-005); workflow_ref columns store the Temporal reference.

CREATE SCHEMA IF NOT EXISTS environment;
CREATE SCHEMA IF NOT EXISTS citizen;

-- ---------------------------------------------------------------------------
-- ENV-09: industrial telemetry, compliance, permits, deforestation, carbon, EIA
-- ---------------------------------------------------------------------------
CREATE TABLE environment.telemetry_readings (
    reading_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    facility_id      VARCHAR(64) NOT NULL,
    sensor_id        VARCHAR(64) NOT NULL,
    medium           VARCHAR(8) NOT NULL,          -- AIR | WATER | NOISE
    parameter        VARCHAR(16) NOT NULL,         -- PM2_5 | NO2 | BOD | COD | PH ...
    value            NUMERIC(18, 6) NOT NULL,
    unit             VARCHAR(16) NOT NULL,
    measured_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE environment.compliance_limits (
    limit_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    medium           VARCHAR(8) NOT NULL,
    parameter        VARCHAR(16) NOT NULL,
    warning_value    NUMERIC(18, 6) NOT NULL,
    violation_value  NUMERIC(18, 6) NOT NULL,
    fine_multiplier  NUMERIC(8, 4) NOT NULL DEFAULT 1.0,  -- state fine multiplier
    UNIQUE (tenant_state_id, medium, parameter)
);

CREATE TABLE environment.compliance_incidents (
    incident_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    reading_id       UUID NOT NULL REFERENCES environment.telemetry_readings (reading_id),
    facility_id      VARCHAR(64) NOT NULL,
    severity         VARCHAR(16) NOT NULL,         -- WARNING | VIOLATION
    fine_estimate_kobo BIGINT NOT NULL DEFAULT 0,
    status           VARCHAR(16) NOT NULL DEFAULT 'OPEN',  -- OPEN | RESOLVED
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE environment.permits (
    permit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    permit_type      VARCHAR(24) NOT NULL,         -- EFFLUENT_DISCHARGE | TIMBER_LOGGING
    holder_ref       VARCHAR(128) NOT NULL,
    facility_id      VARCHAR(64),
    fee_kobo         BIGINT NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'DRAFT',  -- DRAFT | ACTIVE | SUSPENDED | EXPIRED
    issued_at        TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE environment.deforestation_alerts (
    alert_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    polygon          GEOMETRY(Polygon, 4326) NOT NULL,
    h3_cells         TEXT[] NOT NULL DEFAULT '{}', -- H3 hexagonal cell IDs
    ndvi_delta       NUMERIC(6, 4) NOT NULL,
    area_hectares    NUMERIC(12, 4) NOT NULL,
    source           VARCHAR(16) NOT NULL,         -- SENTINEL2 | LANDSAT
    status           VARCHAR(16) NOT NULL DEFAULT 'RAISED',  -- RAISED | DISPATCHED | RESOLVED
    sla_deadline     TIMESTAMPTZ NOT NULL,         -- detected_at + 4h response SLA
    enforcement_ticket_ref VARCHAR(80),            -- SEC-10 CAD ticket reference
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE environment.carbon_projects (
    project_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    name             VARCHAR(256) NOT NULL,
    boundary         GEOMETRY(MultiPolygon, 4326),
    vintage_year     INTEGER NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'REGISTERED',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE environment.carbon_credits (
    credit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    project_id       UUID NOT NULL REFERENCES environment.carbon_projects (project_id),
    serial           VARCHAR(80) NOT NULL UNIQUE,  -- unique registry serial
    quantity_tco2e   NUMERIC(14, 4) NOT NULL,
    vintage_year     INTEGER NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'ISSUED',  -- ISSUED | TRANSFERRED | RETIRED (terminal)
    holder_ref       VARCHAR(128) NOT NULL,
    brokerage_fee_kobo BIGINT NOT NULL DEFAULT 0,  -- [DERIVED] default, state override
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE environment.eia_applications (
    application_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    project_ref      VARCHAR(128) NOT NULL,
    facility_id      VARCHAR(64),
    category         VARCHAR(32) NOT NULL,
    documents        JSONB NOT NULL DEFAULT '[]',
    status           VARCHAR(16) NOT NULL DEFAULT 'SUBMITTED',  -- SUBMITTED | SCREENING | PUBLIC_COMMENT | APPROVED | REJECTED
    decision_reason  TEXT,
    workflow_ref     VARCHAR(80),                  -- Temporal workflow reference
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_env_telemetry_tenant_facility ON environment.telemetry_readings (tenant_state_id, facility_id, measured_at);
CREATE INDEX idx_env_incidents_tenant          ON environment.compliance_incidents (tenant_state_id, status);
CREATE INDEX idx_env_permits_tenant            ON environment.permits (tenant_state_id, status);
CREATE INDEX idx_env_alerts_tenant_status      ON environment.deforestation_alerts (tenant_state_id, status);
CREATE INDEX idx_env_alerts_polygon            ON environment.deforestation_alerts USING GiST (polygon);
CREATE INDEX idx_env_credits_tenant_project    ON environment.carbon_credits (tenant_state_id, project_id);
CREATE INDEX idx_env_eias_tenant               ON environment.eia_applications (tenant_state_id, status);

-- ---------------------------------------------------------------------------
-- CIT-11: identity wallets, service requests, petitions, civil-service audit
-- ---------------------------------------------------------------------------
CREATE TABLE citizen.identity_wallets (
    wallet_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    nin_hash         VARCHAR(128) NOT NULL,        -- salted hash only; raw NIN never stored/exposed
    keycloak_realm   VARCHAR(64) NOT NULL,
    keycloak_client  VARCHAR(64) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, nin_hash)
);

CREATE TABLE citizen.service_requests (
    request_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    wallet_id        UUID NOT NULL REFERENCES citizen.identity_wallets (wallet_id),
    service_code     VARCHAR(48) NOT NULL,         -- catalog entry (revenue, lands, health, ...)
    form_payload     JSONB NOT NULL DEFAULT '{}',
    priority         VARCHAR(16) NOT NULL DEFAULT 'STANDARD',  -- STANDARD | EXPEDITED
    fee_kobo         BIGINT NOT NULL DEFAULT 0,
    status           VARCHAR(16) NOT NULL DEFAULT 'SUBMITTED', -- SUBMITTED | IN_REVIEW | APPROVED | REJECTED | COMPLETED
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE citizen.petitions (
    petition_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    public_ref       VARCHAR(32) NOT NULL UNIQUE,  -- citizen-facing reference
    subject          VARCHAR(256) NOT NULL,
    body             TEXT NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'SUBMITTED',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE citizen.civil_servants (
    servant_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id      VARCHAR(10) NOT NULL,
    employee_number      VARCHAR(32) NOT NULL,
    mda_code             VARCHAR(32) NOT NULL,
    grade_band           VARCHAR(16) NOT NULL,
    salary_kobo          BIGINT NOT NULL,
    biometric_template_hash VARCHAR(128),          -- hash only; no raw biometrics
    bank_account_hash    VARCHAR(128),
    status               VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | INACTIVE | RETIRED
    UNIQUE (tenant_state_id, employee_number)
);

CREATE TABLE citizen.biometric_verifications (
    verification_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    servant_id       UUID NOT NULL REFERENCES citizen.civil_servants (servant_id),
    liveness_passed  BOOLEAN NOT NULL,
    verified         BOOLEAN NOT NULL,
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE citizen.payroll_audits (
    audit_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id      VARCHAR(10) NOT NULL,
    period               VARCHAR(16) NOT NULL,     -- e.g. 2026-09
    staff_scanned        INTEGER NOT NULL DEFAULT 0,
    findings_count       INTEGER NOT NULL DEFAULT 0,
    recoverable_kobo     BIGINT NOT NULL DEFAULT 0,
    workflow_ref         VARCHAR(80),              -- Temporal durable cleanup workflow
    status               VARCHAR(16) NOT NULL DEFAULT 'RUNNING',  -- RUNNING | COMPLETED
    created_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE citizen.ghost_worker_findings (
    finding_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    audit_id         UUID NOT NULL REFERENCES citizen.payroll_audits (audit_id),
    servant_id       UUID NOT NULL REFERENCES citizen.civil_servants (servant_id),
    rule             VARCHAR(40) NOT NULL,         -- UNVERIFIED | DUPLICATE_BIOMETRIC | DUPLICATE_ACCOUNT | INACTIVE_PAID
    recoverable_kobo BIGINT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cit_requests_tenant   ON citizen.service_requests (tenant_state_id, status);
CREATE INDEX idx_cit_petitions_tenant  ON citizen.petitions (tenant_state_id, status);
CREATE INDEX idx_cit_servants_tenant   ON citizen.civil_servants (tenant_state_id, mda_code);
CREATE INDEX idx_cit_findings_audit    ON citizen.ghost_worker_findings (tenant_state_id, audit_id);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security (same session variable as 0001)
-- ---------------------------------------------------------------------------
ALTER TABLE environment.telemetry_readings     ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.compliance_limits      ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.compliance_incidents   ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.permits                ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.deforestation_alerts   ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.carbon_projects        ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.carbon_credits         ENABLE ROW LEVEL SECURITY;
ALTER TABLE environment.eia_applications       ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.identity_wallets           ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.service_requests           ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.petitions                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.civil_servants             ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.biometric_verifications    ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.payroll_audits             ENABLE ROW LEVEL SECURITY;
ALTER TABLE citizen.ghost_worker_findings      ENABLE ROW LEVEL SECURITY;

CREATE POLICY telemetry_readings_tenant_isolation ON environment.telemetry_readings
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY compliance_limits_tenant_isolation ON environment.compliance_limits
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY compliance_incidents_tenant_isolation ON environment.compliance_incidents
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY permits_tenant_isolation ON environment.permits
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY deforestation_alerts_tenant_isolation ON environment.deforestation_alerts
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY carbon_projects_tenant_isolation ON environment.carbon_projects
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY carbon_credits_tenant_isolation ON environment.carbon_credits
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY eia_applications_tenant_isolation ON environment.eia_applications
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY identity_wallets_tenant_isolation ON citizen.identity_wallets
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY service_requests_tenant_isolation ON citizen.service_requests
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY petitions_tenant_isolation ON citizen.petitions
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY civil_servants_tenant_isolation ON citizen.civil_servants
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY biometric_verifications_tenant_isolation ON citizen.biometric_verifications
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY payroll_audits_tenant_isolation ON citizen.payroll_audits
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY ghost_worker_findings_tenant_isolation ON citizen.ghost_worker_findings
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
