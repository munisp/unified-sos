"""Postgres repository tests for mod-kyc-kyb (0005 schema wiring)."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domain import AuditEntry, KycCase, SubjectType, VerificationStatus, sha256_hex
from app.pg_repository import PostgresKycKybRepository, build_kyc_kyb_repository


class FakeCursor:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchone(self):
        return self._one if self._one is not None else (self._rows[0] if self._rows else None)

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.responses:
            return self.responses.pop(0)
        return FakeCursor()


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def make_case():
    return KycCase(case_id="case-1", tenant_state_id="lagos",
                   subject_ref="resident-raw-ref",
                   subject_type=SubjectType.RESIDENT)


def test_save_case_hashes_subject_ref():
    conn = FakeConnection()
    repo = PostgresKycKybRepository(conn=conn)
    repo.save_kyc_case(make_case())
    sql, params = conn.calls[0]
    assert "INSERT INTO kyc_kyb.kyc_cases" in sql
    assert params[3] == sha256_hex("resident-raw-ref")
    assert "resident-raw-ref" not in repr(params)


def test_get_case_round_trip():
    row = {
        "case_id": "case-1", "tenant_state_id": "lagos",
        "subject_type": "RESIDENT", "subject_ref_hash": "ab" * 32,
        "status": "DRAFT", "risk_score": 0, "risk_band": "LOW",
        "required_documents": [], "liveness_required": True,
        "decision_reason": None, "created_at": NOW, "updated_at": NOW,
    }
    repo = PostgresKycKybRepository(conn=FakeConnection([FakeCursor(one=row)]))
    case = repo.get_kyc_case("case-1", "lagos")
    assert case.status is VerificationStatus.DRAFT
    assert case.subject_ref == ""  # raw ref is never persisted


def test_append_audit_genesis_and_insert():
    conn = FakeConnection([FakeCursor(one=None), FakeCursor()])
    repo = PostgresKycKybRepository(conn=conn)
    entry = AuditEntry(
        seq=1, tenant_state_id="lagos", actor="officer-1", action="CASE_OPENED",
        entity_type="kyc_case", entity_id="case-1", detail_hash="cc" * 32,
        prev_hash="GENESIS", entry_hash="dd" * 32,
    )
    repo.append_audit(entry)
    assert "INSERT INTO kyc_kyb.audit_entries" in conn.calls[-1][0]


def test_append_audit_rejects_broken_chain():
    conn = FakeConnection([FakeCursor(one={"entry_hash": "ee" * 32})])
    repo = PostgresKycKybRepository(conn=conn)
    entry = AuditEntry(
        seq=2, tenant_state_id="lagos", actor="a", action="X",
        entity_type="kyc_case", entity_id="case-1", detail_hash="cc" * 32,
        prev_hash="GENESIS", entry_hash="dd" * 32,
    )
    with pytest.raises(ValueError, match="prev_hash"):
        repo.append_audit(entry)


def test_build_repository_fail_closed_in_live_mode():
    with pytest.raises(RuntimeError, match="KYC_KYB_DSN"):
        build_kyc_kyb_repository({"KYC_KYB_MODE": "live"})
    assert build_kyc_kyb_repository({}) is None


@pytest.mark.skipif(
    not os.environ.get("KYC_KYB_TEST_DSN"),
    reason="no KYC_KYB_TEST_DSN — live Postgres round-trip skipped",
)
def test_live_case_round_trip():  # pragma: no cover
    repo = PostgresKycKybRepository(os.environ["KYC_KYB_TEST_DSN"])
    repo.save_kyc_case(make_case())
    fetched = repo.get_kyc_case("case-1", "lagos")
    assert fetched is not None and fetched.tenant_state_id == "lagos"
