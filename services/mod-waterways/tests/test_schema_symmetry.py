"""Schema-symmetry: mod-waterways domain models vs 0010_waterways.sql."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domain import Dredger, Route, RoyaltyAssessment, Survey, Ticket, Trip

MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "db", "migrations", "0010_waterways.sql"
)
SQL = open(MIGRATION).read()


def columns(table: str) -> set:
    match = re.search(rf"CREATE TABLE waterways\.{table} \((.*?)\);", SQL, re.DOTALL)
    assert match, f"table waterways.{table} missing"
    cols = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.startswith(("UNIQUE", "PRIMARY")):
            continue
        cols.add(line.split()[0])
    return cols


def check(model, table, extra=(), dropped=()):
    cols = columns(table)
    for field in model.model_fields:
        if field in dropped:
            continue
        assert field in cols, f"{model.__name__}.{field} missing in {table}"
    for col in extra:
        assert col in cols


def test_routes():
    check(Route, "routes")


def test_trips():
    check(Trip, "trips")


def test_tickets_idempotency_columns():
    check(Ticket, "tickets", extra=("idempotency_key", "request_hash"))
    assert "UNIQUE (tenant_state_id, idempotency_key)" in SQL


def test_dredgers():
    check(Dredger, "dredgers")


def test_surveys_dedupe():
    check(Survey, "surveys", extra=("dedupe_key",))
    assert "UNIQUE (tenant_state_id, dedupe_key)" in SQL


def test_royalty_assessments_hash_chain():
    check(RoyaltyAssessment, "royalty_assessments",
          extra=("prev_hash", "event_hash"))
