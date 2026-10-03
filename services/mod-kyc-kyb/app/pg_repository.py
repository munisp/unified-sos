"""Postgres repository for mod-kyc-kyb (migration 0005, schema ``kyc_kyb``).

Wires the existing 0005 schema: ``kyc_cases`` + the hash-chained
``audit_entries``. NDPA posture preserved at the boundary: the model's
``subject_ref`` is persisted only as ``subject_ref_hash`` (SHA-256), matching
the migration — the raw subject reference is never stored.

Selected by ``KYC_KYB_DSN``; ``KYC_KYB_MODE=live`` without a DSN fails closed
(mirrors the existing registry-adapter idiom in main.py). ``psycopg`` (v3) is
imported lazily so fixture-profile tests never import the driver; a duck-typed
connection may be injected for tests.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .domain import AuditEntry, KycCase, RiskBand, VerificationStatus, sha256_hex


class PostgresKycKybRepository:
    """Cases + audit over the 0005 schema (RLS session var per 0001-0006)."""

    def __init__(self, dsn: str = "", conn=None) -> None:
        if not dsn and conn is None:
            raise ValueError("PostgresKycKybRepository requires a DSN or connection")
        self._dsn = dsn
        self._conn = conn

    def _connect(self):
        import psycopg  # lazy: fixture deployments never import the driver

        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True)

    def _execute(self, sql: str, params: tuple = ()):
        if self._conn is not None:
            return self._conn.execute(sql, params)
        with self._connect() as conn:
            return conn.execute(sql, params)

    # -- KYC cases ----------------------------------------------------------------
    def save_kyc_case(self, case: KycCase) -> KycCase:
        tenant = getattr(case.tenant_state_id, "value", case.tenant_state_id)
        self._execute(
            """
            INSERT INTO kyc_kyb.kyc_cases
                (case_id, tenant_state_id, subject_type, subject_ref_hash,
                 status, risk_score, risk_band, required_documents,
                 liveness_required, decision_reason, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (case_id) DO UPDATE SET
                status = EXCLUDED.status,
                risk_score = EXCLUDED.risk_score,
                risk_band = EXCLUDED.risk_band,
                decision_reason = EXCLUDED.decision_reason,
                updated_at = EXCLUDED.updated_at
            """,
            (
                case.case_id, tenant,
                getattr(case.subject_type, "value", case.subject_type),
                sha256_hex(case.subject_ref),
                getattr(case.status, "value", case.status),
                case.risk_score,
                getattr(case.risk_band, "value", case.risk_band),
                [getattr(d, "value", d) for d in case.required_documents],
                case.liveness_required, case.decision_reason,
                case.created_at, case.updated_at,
            ),
        )
        return case

    def get_kyc_case(self, case_id: str, tenant: str) -> Optional[KycCase]:
        row = self._execute(
            "SELECT * FROM kyc_kyb.kyc_cases "
            "WHERE case_id = %s AND tenant_state_id = %s",
            (case_id, tenant),
        ).fetchone()
        if row is None:
            return None
        return KycCase(
            case_id=str(row["case_id"]), tenant_state_id=row["tenant_state_id"],
            subject_ref="",  # raw ref never stored; only subject_ref_hash persists
            subject_type=row["subject_type"],
            status=VerificationStatus(row["status"]),
            risk_score=row["risk_score"],
            risk_band=RiskBand(row["risk_band"]) if row["risk_band"] else None,
            required_documents=list(row["required_documents"]),
            liveness_required=row["liveness_required"],
            decision_reason=row["decision_reason"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # -- hash-chained audit -------------------------------------------------------------
    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        prev = self._audit_tail()
        expected = prev if prev is not None else "GENESIS"
        if entry.prev_hash != expected:
            raise ValueError("audit chain prev_hash mismatch (append-only)")
        tenant = getattr(entry.tenant_state_id, "value", entry.tenant_state_id)
        self._execute(
            """
            INSERT INTO kyc_kyb.audit_entries
                (tenant_state_id, sequence, case_id, action, actor,
                 payload_hash, prev_hash, entry_hash, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (tenant, entry.seq, entry.entity_id or None, entry.action,
             entry.actor, entry.detail_hash, entry.prev_hash, entry.entry_hash,
             entry.created_at),
        )
        return entry

    def _audit_tail(self) -> Optional[str]:
        row = self._execute(
            "SELECT entry_hash FROM kyc_kyb.audit_entries "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else None

    def audit_for_tenant(self, tenant: str) -> List[AuditEntry]:
        rows = self._execute(
            "SELECT * FROM kyc_kyb.audit_entries "
            "WHERE tenant_state_id = %s ORDER BY sequence",
            (tenant,),
        ).fetchall()
        return [
            AuditEntry(
                seq=r["sequence"], tenant_state_id=r["tenant_state_id"],
                actor=r["actor"], action=r["action"],
                entity_type="case", entity_id=str(r["case_id"] or ""),
                detail_hash=r["payload_hash"], prev_hash=r["prev_hash"],
                entry_hash=r["entry_hash"], created_at=r["created_at"],
            )
            for r in rows
        ]


def build_kyc_kyb_repository(env: Optional[Dict[str, str]] = None):
    """Select persistence from ``KYC_KYB_DSN`` / ``KYC_KYB_MODE``.

    local/test (default) → ``None`` (caller keeps the in-memory
    ``Repository``); ``KYC_KYB_MODE=live`` without a DSN fails closed.
    """
    env = dict(os.environ if env is None else env)
    dsn = env.get("KYC_KYB_DSN", "")
    if dsn:
        return PostgresKycKybRepository(dsn)
    if env.get("KYC_KYB_MODE") == "live":
        raise RuntimeError(
            "KYC_KYB_MODE=live requires KYC_KYB_DSN "
            "(fail-closed: refusing to boot on the in-memory repository)"
        )
    return None
