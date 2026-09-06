-- SOS Migration 0005 — KYC/KYB, Document AI & Liveness domain
-- v3.0 · PostgreSQL 16+ / PostGIS 3.4+ · Tenant-isolated via Row-Level Security
-- Complements 0001–0004.
-- NDPA data-minimization: raw NIN, CAC documents, biometrics, images, videos,
-- and OCR text are NEVER stored in these tables. Only object URIs, SHA-256
-- hashes, extracted minimum fields, scores, and confidence values persist.

CREATE SCHEMA IF NOT EXISTS kyc_kyb;

-- ---------------------------------------------------------------------------
-- KYC: cases, document artifacts, extraction results
-- ---------------------------------------------------------------------------
CREATE TABLE kyc_kyb.kyc_cases (
    case_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    subject_type     VARCHAR(24) NOT NULL,        -- CITIZEN | AGENT | CIVIL_SERVANT | MDA_OFFICER | TRADER | MINER | TRANSPORTER | HEALTH_USER | EDUCATION_USER | VENDOR | AUDITOR | CONCESSIONAIRE
    subject_ref_hash CHAR(64) NOT NULL,           -- SHA-256 of wallet/resident/agent/employee id
    status           VARCHAR(20) NOT NULL DEFAULT 'DRAFT',  -- DRAFT | EVIDENCE_PENDING | PROCESSING | IN_REVIEW | APPROVED | REJECTED | EXPIRED | SUSPENDED
    risk_score       INTEGER NOT NULL DEFAULT 0,  -- 0-100
    risk_band        VARCHAR(12) NOT NULL DEFAULT 'LOW',    -- LOW | MEDIUM | HIGH | PROHIBITED
    required_documents TEXT[] NOT NULL DEFAULT '{}',
    liveness_required BOOLEAN NOT NULL DEFAULT TRUE,
    decision_reason  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kyc_kyb.document_artifacts (
    artifact_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    owner_case_id    UUID NOT NULL,               -- kyc_cases.case_id or kyb_cases.case_id
    owner_kind       VARCHAR(8) NOT NULL,         -- KYC | KYB
    document_type    VARCHAR(40) NOT NULL,        -- NIN_SLIP | NATIONAL_ID | PASSPORT | DRIVERS_LICENSE | VOTER_CARD | PROOF_OF_ADDRESS | CAC_CERTIFICATE | TAX_CLEARANCE | BOARD_RESOLUTION | BENEFICIAL_OWNERSHIP_DECLARATION | OTHER
    object_uri       TEXT NOT NULL,               -- object-store URI; raw bytes never in DB
    sha256           CHAR(64) NOT NULL,
    uploaded_by      VARCHAR(128) NOT NULL,
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expiry_date      DATE,
    UNIQUE (tenant_state_id, sha256)              -- artifact hash reuse detection
);

CREATE TABLE kyc_kyb.extraction_results (
    extraction_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    artifact_id      UUID NOT NULL REFERENCES kyc_kyb.document_artifacts (artifact_id),
    engine           VARCHAR(12) NOT NULL,        -- PADDLEOCR | DOCLING | VLM | SIMULATED
    fields           JSONB NOT NULL DEFAULT '{}', -- extracted minimum fields only
    confidence       NUMERIC(5, 4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    model_version    VARCHAR(64),
    latency_ms       INTEGER,
    warnings         TEXT[] NOT NULL DEFAULT '{}',
    source_hash      CHAR(64) NOT NULL,
    mismatch_flags   TEXT[] NOT NULL DEFAULT '{}', -- VLM vs OCR/Docling adjudication mismatches
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Liveness: challenges, evidence, results (scores/hashes only, never media)
-- ---------------------------------------------------------------------------
CREATE TABLE kyc_kyb.liveness_challenges (
    challenge_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    case_id          UUID NOT NULL REFERENCES kyc_kyb.kyc_cases (case_id),
    nonce            CHAR(64) NOT NULL UNIQUE,
    mode             VARCHAR(8) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | PASSIVE
    action_sequence  TEXT[] NOT NULL,             -- BLINK | TURN_LEFT | TURN_RIGHT | SMILE | SPEAK_PASSPHRASE
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    attempts         INTEGER NOT NULL DEFAULT 0,
    status           VARCHAR(12) NOT NULL DEFAULT 'OPEN',   -- OPEN | PASSED | FAILED | EXPIRED
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kyc_kyb.liveness_evidence (
    evidence_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    challenge_id     UUID NOT NULL REFERENCES kyc_kyb.liveness_challenges (challenge_id),
    artifact_hashes  TEXT[] NOT NULL,             -- SHA-256 hashes of frame/video artifacts
    nonce            CHAR(64) NOT NULL,
    motion_score     NUMERIC(5, 4) CHECK (motion_score BETWEEN 0 AND 1),
    texture_score    NUMERIC(5, 4) CHECK (texture_score BETWEEN 0 AND 1),
    depth_score      NUMERIC(5, 4) CHECK (depth_score BETWEEN 0 AND 1),
    voice_match_score NUMERIC(5, 4) CHECK (voice_match_score BETWEEN 0 AND 1),
    device_attestation_score NUMERIC(5, 4) CHECK (device_attestation_score BETWEEN 0 AND 1),
    captured_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE kyc_kyb.liveness_results (
    result_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    challenge_id     UUID NOT NULL REFERENCES kyc_kyb.liveness_challenges (challenge_id),
    evidence_id      UUID REFERENCES kyc_kyb.liveness_evidence (evidence_id),
    passed           BOOLEAN NOT NULL,
    score            NUMERIC(5, 4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    anti_spoof_flags TEXT[] NOT NULL DEFAULT '{}',
    model_version    VARCHAR(64),
    reasons          TEXT[] NOT NULL DEFAULT '{}',
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- KYB: cases, beneficial owners, registry verifications
-- ---------------------------------------------------------------------------
CREATE TABLE kyc_kyb.kyb_cases (
    case_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    legal_name       VARCHAR(256) NOT NULL,
    rc_number_hash   CHAR(64) NOT NULL,           -- SHA-256 of RC number
    tin_hash         CHAR(64),                    -- SHA-256 of TIN, optional
    business_type    VARCHAR(40) NOT NULL,
    address          TEXT,
    address_point    GEOMETRY(Point, 4326),       -- PostGIS business location
    status           VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    risk_score       INTEGER NOT NULL DEFAULT 0,
    risk_band        VARCHAR(12) NOT NULL DEFAULT 'LOW',
    directors        JSONB NOT NULL DEFAULT '[]', -- name/hash references only
    registry_status  VARCHAR(12),                 -- MATCH | MISMATCH | NOT_FOUND | UNAVAILABLE
    decision_reason  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kyc_kyb.beneficial_owners (
    owner_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    kyb_case_id      UUID NOT NULL REFERENCES kyc_kyb.kyb_cases (case_id),
    name_hash        CHAR(64) NOT NULL,           -- SHA-256 of owner name
    ownership_pct    NUMERIC(5, 2) NOT NULL CHECK (ownership_pct > 0 AND ownership_pct <= 100),
    kyc_case_id      UUID REFERENCES kyc_kyb.kyc_cases (case_id),  -- cross-reference
    politically_exposed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kyc_kyb.registry_verifications (
    verification_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    kyb_case_id      UUID NOT NULL REFERENCES kyc_kyb.kyb_cases (case_id),
    registry         VARCHAR(12) NOT NULL,        -- CAC | NIMC | TAX | SANCTIONS
    status           VARCHAR(12) NOT NULL,        -- MATCH | MISMATCH | NOT_FOUND | UNAVAILABLE
    fields_checked   TEXT[] NOT NULL DEFAULT '{}',
    confidence       NUMERIC(5, 4) CHECK (confidence BETWEEN 0 AND 1),
    response_hash    CHAR(64),                    -- hashed registry response; raw payload never stored
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Review workflow + hash-chained audit
-- ---------------------------------------------------------------------------
CREATE TABLE kyc_kyb.review_tasks (
    task_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    case_id          UUID NOT NULL,
    case_kind        VARCHAR(8) NOT NULL,         -- KYC | KYB
    reason_codes     TEXT[] NOT NULL DEFAULT '{}',
    status           VARCHAR(12) NOT NULL DEFAULT 'OPEN',  -- OPEN | APPROVED | REJECTED
    assignee         VARCHAR(128),
    decision_reason  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at       TIMESTAMPTZ
);

CREATE TABLE kyc_kyb.audit_entries (
    entry_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    sequence         BIGINT NOT NULL,
    case_id          UUID,
    action           VARCHAR(64) NOT NULL,
    actor            VARCHAR(128) NOT NULL,
    payload_hash     CHAR(64) NOT NULL,
    prev_hash        CHAR(64) NOT NULL,           -- hash chain: SHA-256(prev_hash || payload_hash)
    entry_hash       CHAR(64) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_state_id, sequence)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_kyc_cases_tenant_status   ON kyc_kyb.kyc_cases (tenant_state_id, status);
CREATE INDEX idx_kyc_cases_subject         ON kyc_kyb.kyc_cases (tenant_state_id, subject_ref_hash);
CREATE INDEX idx_artifacts_owner           ON kyc_kyb.document_artifacts (tenant_state_id, owner_case_id);
CREATE INDEX idx_extractions_artifact      ON kyc_kyb.extraction_results (tenant_state_id, artifact_id);
CREATE INDEX idx_liveness_ch_case          ON kyc_kyb.liveness_challenges (tenant_state_id, case_id);
CREATE INDEX idx_liveness_ev_challenge     ON kyc_kyb.liveness_evidence (tenant_state_id, challenge_id);
CREATE INDEX idx_liveness_res_challenge    ON kyc_kyb.liveness_results (tenant_state_id, challenge_id);
CREATE INDEX idx_kyb_cases_tenant_status   ON kyc_kyb.kyb_cases (tenant_state_id, status);
CREATE INDEX idx_kyb_cases_rc              ON kyc_kyb.kyb_cases (tenant_state_id, rc_number_hash);
CREATE INDEX idx_beneficial_owners_case    ON kyc_kyb.beneficial_owners (tenant_state_id, kyb_case_id);
CREATE INDEX idx_registry_verifications    ON kyc_kyb.registry_verifications (tenant_state_id, kyb_case_id);
CREATE INDEX idx_review_tasks_queue        ON kyc_kyb.review_tasks (tenant_state_id, status);
CREATE INDEX idx_audit_entries_case        ON kyc_kyb.audit_entries (tenant_state_id, case_id, sequence);
CREATE INDEX idx_kyb_cases_address_point   ON kyc_kyb.kyb_cases USING GIST (address_point);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security (same session variable as 0001-0004)
-- ---------------------------------------------------------------------------
ALTER TABLE kyc_kyb.kyc_cases               ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.document_artifacts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.extraction_results      ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.liveness_challenges     ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.liveness_evidence       ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.liveness_results        ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.kyb_cases               ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.beneficial_owners       ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.registry_verifications  ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.review_tasks            ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_kyb.audit_entries           ENABLE ROW LEVEL SECURITY;

CREATE POLICY kyc_cases_tenant_isolation ON kyc_kyb.kyc_cases
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY document_artifacts_tenant_isolation ON kyc_kyb.document_artifacts
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY extraction_results_tenant_isolation ON kyc_kyb.extraction_results
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY liveness_challenges_tenant_isolation ON kyc_kyb.liveness_challenges
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY liveness_evidence_tenant_isolation ON kyc_kyb.liveness_evidence
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY liveness_results_tenant_isolation ON kyc_kyb.liveness_results
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY kyb_cases_tenant_isolation ON kyc_kyb.kyb_cases
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY beneficial_owners_tenant_isolation ON kyc_kyb.beneficial_owners
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY registry_verifications_tenant_isolation ON kyc_kyb.registry_verifications
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY review_tasks_tenant_isolation ON kyc_kyb.review_tasks
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY audit_entries_tenant_isolation ON kyc_kyb.audit_entries
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
