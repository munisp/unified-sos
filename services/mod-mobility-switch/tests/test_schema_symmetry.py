"""Schema-symmetry: mod-mobility-switch models vs 0011_mobility.sql."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domain import ClearingRecord, EscrowRecord, FareTable, SettlementBatch

MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "db", "migrations", "0011_mobility.sql"
)
SQL = open(MIGRATION).read()


def columns(table: str) -> set:
    match = re.search(rf"CREATE TABLE mobility\.{table} \((.*?)\);", SQL, re.DOTALL)
    assert match, f"table mobility.{table} missing"
    cols = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.startswith(("UNIQUE", "PRIMARY")):
            continue
        cols.add(line.split()[0])
    return cols


def check(model, table, extra=()):
    cols = columns(table)
    for field in model.model_fields:
        assert field in cols, f"{model.__name__}.{field} missing in {table}"
    for col in extra:
        assert col in cols


def test_fare_tables():
    check(FareTable, "fare_tables")


def test_clearing_records():
    check(ClearingRecord, "clearing_records")


def test_settlement_batches():
    check(SettlementBatch, "settlement_batches")


def test_escrows():
    check(EscrowRecord, "escrows")


def test_audit_chain_hash_columns():
    cols = columns("audit_chain")
    assert {"event_id", "event_type", "prev_hash", "event_hash"} <= cols
    assert "ENABLE ROW LEVEL SECURITY" in SQL
