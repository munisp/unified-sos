"""Postgres repository tests for mod-mobility-switch escrows/batches."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domain import EscrowRecord, EscrowState, SettlementBatch, SettlementLeg
from app.adapters.pg_repository import (
    AdapterUnavailableError,
    PostgresEscrowRepository,
    build_mobility_repository,
)


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


def make_escrow(state=EscrowState.PENDING):
    return EscrowRecord(
        transfer_id="tr-1", batch_id="stl-1", amount_kobo=1000,
        condition="cond", state=state, created_at="2025-01-01T00:00:00+00:00",
        expires_at="2025-01-01T00:15:00+00:00")


ESCROW_ROW = {
    "transfer_id": "tr-1", "batch_id": "stl-1", "amount_kobo": 1000,
    "condition": "cond", "state": "pending", "fulfilment": None,
    "created_at": "2025-01-01T00:00:00+00:00",
    "expires_at": "2025-01-01T00:15:00+00:00", "completed_at": None,
}


def test_begin_escrow_inserts_when_absent():
    conn = FakeConnection([FakeCursor(one=None), FakeCursor()])
    repo = PostgresEscrowRepository(conn=conn)
    record, created = repo.begin_escrow(make_escrow())
    assert created is True and record.transfer_id == "tr-1"
    assert "INSERT INTO mobility.escrows" in conn.calls[1][0]


def test_begin_escrow_replay_returns_stored():
    conn = FakeConnection([FakeCursor(one=dict(ESCROW_ROW))])
    repo = PostgresEscrowRepository(conn=conn)
    record, created = repo.begin_escrow(make_escrow())
    assert created is False and record.state is EscrowState.PENDING
    assert len(conn.calls) == 1


def test_begin_escrow_divergent_terms_raise():
    conn = FakeConnection([FakeCursor(one=dict(ESCROW_ROW))])
    repo = PostgresEscrowRepository(conn=conn)
    with pytest.raises(ValueError, match="different terms"):
        repo.begin_escrow(make_escrow().__class__(
            **{**make_escrow().model_dump(), "amount_kobo": 9999}))


def test_update_escrow_state_posts_fulfilment():
    conn = FakeConnection()
    repo = PostgresEscrowRepository(conn=conn)
    repo.update_escrow_state("tr-1", EscrowState.POSTED, "fulfil-x",
                             "2025-01-01T00:10:00+00:00")
    sql, params = conn.calls[0]
    assert "UPDATE mobility.escrows" in sql
    assert params[0] == "posted" and params[1] == "fulfil-x"


def test_batch_round_trip():
    row = {
        "batch_id": "stl-1", "tenant_state_id": "lagos", "operator_id": "op-1",
        "record_count": 2, "gross_kobo": 200000,
        "legs": [{"beneficiary": "TRANSPORT_UNION_COMMISSION",
                  "tigerbeetle_account_code": 4002, "amount_kobo": 10000,
                  "transfer_code": 140}],
        "created_at": "2025-01-01T00:00:00+00:00",
    }
    conn = FakeConnection([FakeCursor(one=row)])
    repo = PostgresEscrowRepository(conn=conn)
    batch = repo.get_batch("stl-1")
    assert batch.gross_kobo == 200000
    assert batch.legs[0].tigerbeetle_account_code == 4002


def test_build_repository_fail_closed_in_production():
    with pytest.raises(AdapterUnavailableError):
        build_mobility_repository({"SOS_PROFILE": "production"})
    assert build_mobility_repository({}) is None


@pytest.mark.skipif(
    not os.environ.get("SOS_MOBILITY_TEST_DSN"),
    reason="no SOS_MOBILITY_TEST_DSN — live Postgres round-trip skipped",
)
def test_live_escrow_idempotency():  # pragma: no cover
    repo = PostgresEscrowRepository(os.environ["SOS_MOBILITY_TEST_DSN"])
    _, first = repo.begin_escrow(make_escrow())
    _, second = repo.begin_escrow(make_escrow())
    assert first is True and second is False
