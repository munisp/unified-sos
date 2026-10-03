"""Schema-symmetry: mod-identity models vs db/migrations/0007_identity.sql.

Parse-check only (no database): every persisted model field must have a
column, with the documented NDPA renames (state_id → tenant_state_id,
nin → nin_hash + nin_tail).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models import ApiConsumer, AuditEntry, ConsentGrant, Credential, GuardianLink, Resident

MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "db", "migrations", "0007_identity.sql"
)


def columns(table: str) -> set:
    sql = open(MIGRATION).read()
    match = re.search(
        rf"CREATE TABLE identity\.{table} \((.*?)\);", sql, re.DOTALL
    )
    assert match, f"table identity.{table} not found in 0007"
    cols = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.startswith("UNIQUE") or line.startswith("PRIMARY"):
            continue
        cols.add(line.split()[0])
    return cols


FIELD_MAP = {"state_id": "tenant_state_id"}


def assert_fields_covered(model, table, extra_map=None, dropped=()):
    mapping = dict(FIELD_MAP)
    mapping.update(extra_map or {})
    cols = columns(table)
    for field in model.model_fields:
        if field in dropped:
            continue
        col = mapping.get(field, field)
        assert col in cols, f"{model.__name__}.{field} -> missing column {table}.{col}"


def test_residents_columns_cover_model():
    assert_fields_covered(Resident, "residents", extra_map={"nin": "nin_hash"})
    # NDPA: raw NIN column must NOT exist
    assert "nin" not in columns("residents")
    assert "nin_hash" in columns("residents")
    assert "nin_tail" in columns("residents")


def test_credentials_columns_cover_model():
    assert_fields_covered(Credential, "credentials")


def test_consumers_columns_cover_model():
    assert_fields_covered(ApiConsumer, "api_consumers")


def test_consent_columns_cover_model():
    assert_fields_covered(ConsentGrant, "consent_grants")


def test_guardian_links_columns_cover_model():
    assert_fields_covered(GuardianLink, "guardian_links")


def test_audit_columns_cover_hash_chain():
    cols = columns("audit_entries")
    for col in ("seq", "action", "tenant_state_id", "prev_hash", "entry_hash"):
        assert col in cols
    mapping = {"state_id": "tenant_state_id", "seq": "seq", "at": "at"}
    for field in AuditEntry.model_fields:
        assert mapping.get(field, field) in cols


def test_migration_has_rls_and_hashchain_conventions():
    sql = open(MIGRATION).read()
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.current_state_tenant')" in sql
    assert "prev_hash" in sql and "entry_hash" in sql
