"""Idempotency middleware: replay, conflict-409, TTL, fail-closed seams."""
import pytest

from ledger.fundsflow.idempotency import (
    AdapterUnavailableError,
    IdempotencyConflict,
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
    request_hash,
    select_store,
)


def make_mw(clock=None):
    clock = clock or (lambda: 1000.0)
    return IdempotencyMiddleware(store=InMemoryIdempotencyStore(clock), clock=clock)


def test_replay_returns_original_response_without_reexecution():
    mw = make_mw()
    calls = []

    def handler():
        calls.append(1)
        return {"status": "ok", "receipt": "R1"}

    first = mw.execute("key-1", {"amount": 500}, handler)
    second = mw.execute("key-1", {"amount": 500}, handler)
    assert first == second == {"status": "ok", "receipt": "R1"}
    assert len(calls) == 1 and mw.executions == 1


def test_conflicting_payload_same_key_raises_409():
    mw = make_mw()
    mw.execute("key-2", {"amount": 500}, lambda: "orig")
    with pytest.raises(IdempotencyConflict) as exc:
        mw.execute("key-2", {"amount": 999}, lambda: "evil")
    assert exc.value.status_code == 409


def test_ttl_expiry_allows_fresh_execution():
    now = [1000.0]
    mw = IdempotencyMiddleware(
        store=InMemoryIdempotencyStore(lambda: now[0]),
        ttl_seconds=60,
        clock=lambda: now[0],
    )
    mw.execute("key-3", {"a": 1}, lambda: "first")
    now[0] += 61  # past TTL
    assert mw.execute("key-3", {"a": 1}, lambda: "second") == "second"
    assert mw.executions == 2


def test_missing_key_rejected():
    mw = make_mw()
    with pytest.raises(ValueError):
        mw.execute("", {"a": 1}, lambda: None)


def test_request_hash_is_canonical():
    assert request_hash({"b": 2, "a": 1}) == request_hash({"a": 1, "b": 2})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


def test_select_store_fail_closed_in_production(monkeypatch):
    monkeypatch.delenv("SOS_REDIS_URL", raising=False)
    monkeypatch.setenv("SOS_PROFILE", "production")
    with pytest.raises(AdapterUnavailableError):
        select_store()
