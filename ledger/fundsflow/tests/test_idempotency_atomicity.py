"""Idempotency: atomic put_if_absent claim, 409-in-progress, TTL values."""
import threading
import time

import pytest

from ledger.fundsflow.idempotency import (
    DEFAULT_TTL_SECONDS,
    MONEY_TTL_SECONDS,
    NON_MONEY_TTL_SECONDS,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
    StoredResponse,
)


def test_money_ttl_is_7_days_and_default_for_middleware():
    assert MONEY_TTL_SECONDS == 7 * 24 * 3600
    assert NON_MONEY_TTL_SECONDS == 24 * 3600
    assert DEFAULT_TTL_SECONDS == MONEY_TTL_SECONDS
    mw = IdempotencyMiddleware(store=InMemoryIdempotencyStore(lambda: 0.0),
                               clock=lambda: 0.0)
    assert mw.ttl_seconds == MONEY_TTL_SECONDS
    mw.execute("k", {"a": 1}, lambda: "ok")
    rec = mw.store.get("k")
    assert rec.expires_at == MONEY_TTL_SECONDS


def test_non_money_ttl_via_override():
    mw = IdempotencyMiddleware(store=InMemoryIdempotencyStore(lambda: 0.0),
                               clock=lambda: 0.0)
    mw.execute("k2", {"a": 1}, lambda: "ok", ttl_seconds=NON_MONEY_TTL_SECONDS)
    assert mw.store.get("k2").expires_at == NON_MONEY_TTL_SECONDS


def test_put_if_absent_returns_existing_and_is_atomic():
    store = InMemoryIdempotencyStore(lambda: 0.0)
    a = StoredResponse("h", None, 100.0, in_progress=True)
    assert store.put_if_absent("k", a) is None
    b = StoredResponse("h", "done", 100.0)
    assert store.put_if_absent("k", b) is a  # existing returned, not overwritten
    assert store.get("k") is a


def test_put_if_absent_expired_entry_does_not_block():
    now = [0.0]
    store = InMemoryIdempotencyStore(lambda: now[0])
    store.put_if_absent("k", StoredResponse("h", "old", 10.0))
    now[0] = 11.0  # expired
    fresh = StoredResponse("h", None, 100.0, in_progress=True)
    assert store.put_if_absent("k", fresh) is None
    assert store.get("k") is fresh


def test_concurrent_duplicate_executes_exactly_once():
    mw = IdempotencyMiddleware()
    barrier = threading.Barrier(8)
    results, errors = [], []

    def handler():
        time.sleep(0.02)  # widen the race window
        return {"receipt": "R1"}

    def worker():
        barrier.wait()
        try:
            results.append(mw.execute("race-key", {"amount": 500}, handler))
        except IdempotencyConflict as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert mw.executions == 1  # exactly one real execution
    assert len(results) + len(errors) == 8
    assert results and all(r == {"receipt": "R1"} for r in results)
    # losers got 409-in-progress (replay is only valid AFTER completion);
    # a straggler arriving after completion replays instead — both are
    # ConflictOrReplay semantics, never a second execution
    assert all(isinstance(e, IdempotencyInProgress) for e in errors)
    assert all(e.status_code == 409 for e in errors)
    # after completion, replay returns the stored response
    assert mw.execute("race-key", {"amount": 500}, handler) == {"receipt": "R1"}
    assert mw.executions == 1


def test_failed_execution_releases_claim_for_retry():
    mw = IdempotencyMiddleware()

    def bad():
        raise RuntimeError("handler blew up")

    with pytest.raises(RuntimeError):
        mw.execute("fail-key", {"a": 1}, bad)
    assert mw.store.get("fail-key") is None  # claim released
    assert mw.execute("fail-key", {"a": 1}, lambda: "retried") == "retried"
    assert mw.executions == 1


def test_in_progress_with_different_payload_is_conflict_not_in_progress():
    mw = IdempotencyMiddleware()
    mw.store.put_if_absent(
        "c-key", StoredResponse("other-hash", None, time.time() + 60, in_progress=True)
    )
    with pytest.raises(IdempotencyConflict) as exc:
        mw.execute("c-key", {"a": 1}, lambda: None)
    assert not isinstance(exc.value, IdempotencyInProgress)
