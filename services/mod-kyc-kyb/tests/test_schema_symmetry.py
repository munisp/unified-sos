"""Schema-symmetry: mod-kyc-kyb domain models vs db/migrations/0005_kyc_kyb.sql.

Parse-check: the existing 0005 schema must carry every persisted model field
(with the documented NDPA rename subject_ref → subject_ref_hash).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domain import AuditEntry, KycCase

MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "db", "migrations", "0005_kyc_kyb.sql"
)
SQL = open(MIGRATION).read()


def columns(table: str) -> set:
    match = re.search(rf"CREATE TABLE kyc_kyb\.{table} \((.*?)\);", SQL, re.DOTALL)
    assert match, f"table kyc_kyb.{table} missing"
    cols = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.startswith(("UNIQUE", "PRIMARY")):
            continue
        cols.add(line.split()[0])
    return cols


def test_kyc_cases_columns_cover_model():
    cols = columns("kyc_cases")
    mapping = {"subject_ref": "subject_ref_hash"}
    dropped = {"created_by"}  # model-only actor field; not persisted in 0005
    for field in KycCase.model_fields:
        if field in dropped:
            continue
        col = mapping.get(field, field)
        assert col in cols, f"KycCase.{field} -> missing column kyc_cases.{col}"
    assert "subject_ref" not in cols  # NDPA: raw ref never stored


def test_audit_entries_columns_cover_model():
    cols = columns("audit_entries")
    mapping = {"seq": "sequence", "entity_id": "case_id",
               "detail_hash": "payload_hash"}
    dropped = {"entity_type"}  # implied by case_id / case_kind in 0005
    for field in AuditEntry.model_fields:
        if field in dropped:
            continue
        col = mapping.get(field, field)
        assert col in cols, f"AuditEntry.{field} -> missing column audit_entries.{col}"
    assert {"prev_hash", "entry_hash"} <= cols


def test_0005_has_rls():
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "current_setting('app.current_state_tenant')" in SQL
