"""Postgres repository tests for mod-identity (fake connection + skip-if-no-DSN).

The fake connection emulates psycopg3 dict-row cursors and records every
statement, so CRUD paths are exercised without a database. A live round-trip
runs only when SOS_IDENTITY_TEST_DSN is set.
"""
import hashlib
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models import ApiConsumer, AuditEntry, ConsentGrant, Resident, VerificationProduct
from app.repo_pg import (
    PostgresIdentityRepository,
    build_identity_repository,
)
from app.repo import InMemoryIdentityRepository


class FakeCursor:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchone(self):
        return self._one if self._one is not None else (self._rows[0] if self._rows else None)

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    """Duck-typed psycopg3 connection: canned responses in FIFO order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.responses:
            return self.responses.pop(0)
        return FakeCursor()


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def make_resident(**kw):
    return Resident(
        resident_id=kw.get("resident_id", "res-1"), state_id="lagos",
        nin="12345678901", full_name="Ada Lovelace", address="1 Marina",
        registered_at=NOW,
    )


def test_save_resident_hashes_nin_and_never_persists_raw():
    conn = FakeConnection()
    repo = PostgresIdentityRepository(conn=conn)
    repo.save_resident(make_resident())
    sql, params = conn.calls[0]
    assert "INSERT INTO identity.residents" in sql
    assert params[0] == "res-1" and params[1] == "lagos"
    assert params[2] == hashlib.sha256(b"12345678901").hexdigest()
    assert params[3] == "901"  # nin_tail
    assert "12345678901" not in repr(params)


def test_get_resident_round_trip_uses_masked_nin():
    row = {
        "resident_id": "res-1", "tenant_state_id": "lagos",
        "nin_hash": "ab" * 32, "nin_tail": "901", "full_name": "Ada Lovelace",
        "address": "1 Marina", "registered_at": NOW, "active": True,
        "status": "ACTIVE", "date_of_birth": None,
    }
    repo = PostgresIdentityRepository(conn=FakeConnection([FakeCursor(one=row)]))
    resident = repo.get_resident("res-1")
    assert resident.resident_id == "res-1"
    assert resident.masked_nin == "********901"


def test_get_resident_missing_returns_none():
    repo = PostgresIdentityRepository(conn=FakeConnection([FakeCursor(one=None)]))
    assert repo.get_resident("nope") is None


def test_consent_lookup_round_trip():
    row = {
        "grant_id": "g-1", "tenant_state_id": "lagos", "resident_id": "res-1",
        "consumer_id": "c-1", "purpose": "KYC_ADJUNCT", "created_at": NOW,
        "expires_at": NOW, "revoked_at": None,
    }
    repo = PostgresIdentityRepository(conn=FakeConnection([FakeCursor(rows=[row])]))
    grants = repo.find_consent("lagos", "res-1", "c-1", "KYC_ADJUNCT")
    assert len(grants) == 1 and grants[0].purpose is VerificationProduct.KYC_ADJUNCT


def test_append_audit_enforces_chain():
    # tail query -> no rows (genesis), count -> 0
    conn = FakeConnection([FakeCursor(one=None), FakeCursor(one={"n": 0}),
                           FakeCursor()])
    repo = PostgresIdentityRepository(conn=conn)
    entry = AuditEntry(
        seq=1, action="VERIFY_GRANTED", state_id="lagos", actor_id="c-1",
        subject_id="res-1", prev_hash=repo.GENESIS_HASH,
        entry_hash=AuditEntry.compute_hash(repo.GENESIS_HASH, "x"),
    )
    repo.append_audit(entry)
    assert "INSERT INTO identity.audit_entries" in conn.calls[-1][0]


def test_append_audit_rejects_broken_chain():
    conn = FakeConnection([FakeCursor(one={"entry_hash": "ff" * 32}),
                           FakeCursor(one={"n": 3})])
    repo = PostgresIdentityRepository(conn=conn)
    entry = AuditEntry(
        seq=4, action="X", state_id="lagos", actor_id="a", subject_id="s",
        prev_hash="00" * 32, entry_hash="11" * 32,
    )
    with pytest.raises(ValueError, match="prev_hash"):
        repo.append_audit(entry)


def test_build_repository_fail_closed_in_production():
    with pytest.raises(RuntimeError, match="SOS_IDENTITY_DSN"):
        build_identity_repository({"SOS_IDENTITY_PROFILE": "production"})
    assert isinstance(build_identity_repository({}), InMemoryIdentityRepository)
    assert isinstance(
        build_identity_repository({"SOS_IDENTITY_PROFILE": "fixture"}),
        InMemoryIdentityRepository,
    )


@pytest.mark.skipif(
    not os.environ.get("SOS_IDENTITY_TEST_DSN"),
    reason="no SOS_IDENTITY_TEST_DSN — live Postgres round-trip skipped",
)
def test_live_resident_round_trip():  # pragma: no cover
    repo = PostgresIdentityRepository(os.environ["SOS_IDENTITY_TEST_DSN"])
    resident = make_resident(resident_id="res-live-1")
    repo.save_resident(resident)
    fetched = repo.get_resident("res-live-1")
    assert fetched is not None and fetched.state_id == "lagos"
