"""Postgres repository tests for mod-waterways (fake conn + skip-if-no-DSN)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domain import SurveyDuplicateError, Ticket, TicketIdempotencyConflict
from app.repository import PostgresWaterwaysRepository, build_waterways_repository


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


def make_ticket(ticket_id="tkt-1"):
    return Ticket(ticket_id=ticket_id, tenant_state_id="lagos", trip_id="trip-1",
                  passenger_name="Ada", fare_kobo=86000, qr_ref="WWY-LAG-ABC",
                  sold_at="2025-01-01T08:00:00+00:00")


def test_ticket_insert_without_idempotency_key():
    conn = FakeConnection()
    repo = PostgresWaterwaysRepository(conn=conn)
    repo.save_ticket_idempotent(make_ticket(), "lagos", None, None)
    sql, params = conn.calls[0]
    assert "INSERT INTO waterways.tickets" in sql
    assert params[0] == "tkt-1"


def test_ticket_idempotent_replay_returns_stored():
    stored = {
        "ticket_id": "tkt-orig", "tenant_state_id": "lagos", "trip_id": "trip-1",
        "passenger_name": "Ada", "fare_kobo": 86000, "qr_ref": "WWY-LAG-ORIG",
        "sold_at": "2025-01-01T08:00:00+00:00",
    }
    conn = FakeConnection([
        FakeCursor(one={"ticket_id": "tkt-orig", "request_hash": "digest-1"}),
        FakeCursor(one=stored),
    ])
    repo = PostgresWaterwaysRepository(conn=conn)
    ticket = repo.save_ticket_idempotent(
        make_ticket("tkt-new"), "lagos", "idem-1", "digest-1")
    assert ticket.ticket_id == "tkt-orig"  # replayed original
    assert len(conn.calls) == 2            # no INSERT issued


def test_ticket_idempotency_conflict_on_different_payload():
    conn = FakeConnection([
        FakeCursor(one={"ticket_id": "tkt-orig", "request_hash": "digest-A"}),
    ])
    repo = PostgresWaterwaysRepository(conn=conn)
    with pytest.raises(TicketIdempotencyConflict):
        repo.save_ticket_idempotent(make_ticket(), "lagos", "idem-1", "digest-B")


def test_survey_duplicate_raises_409_error():
    conn = FakeConnection([FakeCursor(one=None)])  # ON CONFLICT DO NOTHING → no row
    repo = PostgresWaterwaysRepository(conn=conn)
    from app.domain import Survey

    survey = Survey(survey_id="srv-1", tenant_state_id="lagos", dredger_id="drg-1",
                    polygon=[[3.4, 6.5], [3.5, 6.5], [3.5, 6.6]],
                    volume_m3=100.0, surveyed_at="2025-01-15T00:00:00+00:00",
                    month="2025-01")
    with pytest.raises(SurveyDuplicateError):
        repo.save_survey(survey, "dedupe-key-1")


def test_build_repository_fail_closed_in_production():
    with pytest.raises(RuntimeError, match="SOS_WATERWAYS_DSN"):
        build_waterways_repository({"SOS_WATERWAYS_PROFILE": "production"})
    assert build_waterways_repository({}) is None
    assert build_waterways_repository({"SOS_WATERWAYS_PROFILE": "fixture"}) is None


@pytest.mark.skipif(
    not os.environ.get("SOS_WATERWAYS_TEST_DSN"),
    reason="no SOS_WATERWAYS_TEST_DSN — live Postgres round-trip skipped",
)
def test_live_ticket_idempotent_insert():  # pragma: no cover
    repo = PostgresWaterwaysRepository(os.environ["SOS_WATERWAYS_TEST_DSN"])
    ticket = make_ticket("tkt-live-1")
    repo.save_ticket_idempotent(ticket, "lagos", "idem-live-1", "digest")
    replay = repo.save_ticket_idempotent(ticket, "lagos", "idem-live-1", "digest")
    assert replay.ticket_id == "tkt-live-1"
