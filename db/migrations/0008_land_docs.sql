-- SOS Migration 0008 — Land Documents domain (mod-land-docs)
-- PostgreSQL 16+ · Tenant-isolated via Row-Level Security
-- Document bytes live in object storage; only storage_ref + content_hash
-- (SHA-256) persist. Duplicate detection indexed on (tenant, content_hash).

CREATE SCHEMA IF NOT EXISTS land_docs;

-- ---------------------------------------------------------------------------
-- Documents + immutable version lineage
-- ---------------------------------------------------------------------------
CREATE TABLE land_docs.documents (
    document_id      VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    title            VARCHAR(256) NOT NULL,
    filename         VARCHAR(256) NOT NULL,
    doc_type         VARCHAR(40) NOT NULL,          -- C_OF_O | DEED | SURVEY_PLAN | ...
    status           VARCHAR(24) NOT NULL DEFAULT 'PENDING',  -- PENDING | VERIFIED | REJECTED | SUPERSEDED
    version          INTEGER NOT NULL DEFAULT 1,
    parcel_id        VARCHAR(64),                   -- cadastre.parcels linkage (0001)
    content_hash     CHAR(64) NOT NULL,             -- SHA-256 of stored bytes
    storage_ref      TEXT NOT NULL,                 -- object-store URI; bytes never in DB
    root_document_id VARCHAR(64) NOT NULL,          -- version lineage root
    superseded_by    VARCHAR(64),
    classifier_scores JSONB NOT NULL DEFAULT '{}',
    extracted_fields JSONB NOT NULL DEFAULT '{}',   -- OCR minimum fields only
    ocr_confidence   NUMERIC(5, 4) CHECK (ocr_confidence BETWEEN 0 AND 1),
    needs_manual_review BOOLEAN NOT NULL DEFAULT FALSE,
    verified_by      VARCHAR(128),
    verification_reason TEXT,
    rejection_reason TEXT,
    registered_by    VARCHAR(128) NOT NULL,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE land_docs.document_versions (
    version_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    document_id      VARCHAR(64) NOT NULL REFERENCES land_docs.documents (document_id),
    version          INTEGER NOT NULL,
    content_hash     CHAR(64) NOT NULL,
    storage_ref      TEXT NOT NULL,
    superseded_by    VARCHAR(64),
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, version)
);

-- ---------------------------------------------------------------------------
-- Duplicate-detection index (same content hash inside a tenant = candidate)
-- ---------------------------------------------------------------------------
CREATE TABLE land_docs.duplicate_candidates (
    candidate_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_state_id  VARCHAR(10) NOT NULL,
    document_id      VARCHAR(64) NOT NULL REFERENCES land_docs.documents (document_id),
    matched_document_id VARCHAR(64) NOT NULL REFERENCES land_docs.documents (document_id),
    reason           VARCHAR(64) NOT NULL,          -- CONTENT_HASH | TITLE_SIMILARITY | PARCEL_OVERLAP
    score            NUMERIC(5, 4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, matched_document_id)
);

-- ---------------------------------------------------------------------------
-- Hash-chained audit of document lifecycle actions
-- ---------------------------------------------------------------------------
CREATE TABLE land_docs.audit_records (
    event_id         VARCHAR(64) PRIMARY KEY,
    tenant_state_id  VARCHAR(10) NOT NULL,
    document_id      VARCHAR(64) NOT NULL,
    action           VARCHAR(64) NOT NULL,
    actor            VARCHAR(128) NOT NULL,
    detail           TEXT NOT NULL DEFAULT '',
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prev_hash        CHAR(64) NOT NULL,
    event_hash       CHAR(64) NOT NULL
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_documents_tenant_status   ON land_docs.documents (tenant_state_id, status);
CREATE INDEX idx_documents_parcel          ON land_docs.documents (tenant_state_id, parcel_id);
CREATE INDEX idx_documents_lineage         ON land_docs.documents (tenant_state_id, root_document_id, version);
CREATE INDEX idx_documents_content_hash    ON land_docs.documents (tenant_state_id, content_hash);
CREATE INDEX idx_doc_versions_document     ON land_docs.document_versions (tenant_state_id, document_id);
CREATE INDEX idx_dup_candidates_document   ON land_docs.duplicate_candidates (tenant_state_id, document_id);
CREATE INDEX idx_landdocs_audit_document   ON land_docs.audit_records (tenant_state_id, document_id, recorded_at);

-- ---------------------------------------------------------------------------
-- Tenant isolation via Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE land_docs.documents            ENABLE ROW LEVEL SECURITY;
ALTER TABLE land_docs.document_versions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE land_docs.duplicate_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE land_docs.audit_records        ENABLE ROW LEVEL SECURITY;

CREATE POLICY documents_tenant_isolation ON land_docs.documents
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY document_versions_tenant_isolation ON land_docs.document_versions
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY duplicate_candidates_tenant_isolation ON land_docs.duplicate_candidates
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
CREATE POLICY audit_records_tenant_isolation ON land_docs.audit_records
    FOR ALL USING (tenant_state_id = current_setting('app.current_state_tenant'));
