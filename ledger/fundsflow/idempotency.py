"""Idempotency middleware: key → request-hash → stored response.

Semantics:
* First request with a key executes ``fn`` and stores (request_hash, response).
* Replay with the same key AND same payload hash returns the original
  response without re-executing (no double money movement).
* Same key with a DIFFERENT payload hash → :class:`IdempotencyConflict`
  (HTTP 409 semantics).
* Entries expire after ``ttl_seconds`` (default 24h), mirroring typical
  payment-scheme idempotency windows.

Stores: in-memory default; Redis seam behind ``SOS_REDIS_URL`` with an
import-guarded ``redis`` dependency (fail-closed without it in the
production profile).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol

MONEY_TTL_SECONDS = 7 * 24 * 3600  # money-moving idempotency window: 7 days
NON_MONEY_TTL_SECONDS = 24 * 3600  # non-money window stays at 24h
DEFAULT_TTL_SECONDS = MONEY_TTL_SECONDS  # this middleware guards money movement


class AdapterUnavailableError(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    """Same idempotency key reused with a different payload (HTTP 409)."""

    status_code = 409


class IdempotencyInProgress(IdempotencyConflict):
    """Same key currently executing elsewhere (HTTP 409 in-progress)."""


def request_hash(payload: Any) -> str:
    """Canonical SHA-256 request hash (payload order-independent)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass
class StoredResponse:
    request_hash: str
    response: Any
    expires_at: float
    in_progress: bool = False  # claimed but fn() has not completed yet


class IdempotencyStore(Protocol):
    def get(self, key: str) -> Optional[StoredResponse]: ...

    def put(self, key: str, record: StoredResponse) -> None: ...

    def put_if_absent(self, key: str, record: StoredResponse) -> Optional[StoredResponse]:
        """Atomically store ``record`` iff no live entry exists for ``key``.

        Returns None when the claim succeeded, else the existing record.
        """
        ...

    def delete(self, key: str) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._data: Dict[str, StoredResponse] = {}
        self._clock = clock
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[StoredResponse]:
        with self._lock:
            rec = self._data.get(key)
            if rec is not None and rec.expires_at <= self._clock():
                del self._data[key]  # TTL expiry
                return None
            return rec

    def put(self, key: str, record: StoredResponse) -> None:
        with self._lock:
            self._data[key] = record

    def put_if_absent(self, key: str, record: StoredResponse) -> Optional[StoredResponse]:
        with self._lock:
            existing = self._data.get(key)
            if existing is not None and existing.expires_at <= self._clock():
                del self._data[key]  # expired entries do not block a claim
                existing = None
            if existing is not None:
                return existing
            self._data[key] = record
            return None

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class RedisIdempotencyStore:
    """Redis seam (SET key value PX ttl; value = JSON record). Fail-closed."""

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or os.environ.get("SOS_REDIS_URL")
        if not self.url:
            raise AdapterUnavailableError(
                "redis idempotency store requires SOS_REDIS_URL (fail-closed)"
            )
        try:  # pragma: no cover - optional dependency
            import redis  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AdapterUnavailableError("redis package not installed") from exc
        self._client = redis.Redis.from_url(self.url)  # pragma: no cover

    def get(self, key: str) -> Optional[StoredResponse]:  # pragma: no cover
        raw = self._client.get(f"idem:{key}")
        if raw is None:
            return None
        data = json.loads(raw)
        return StoredResponse(
            data["request_hash"], data["response"], data["expires_at"],
            data.get("in_progress", False),
        )

    def put(self, key: str, record: StoredResponse) -> None:  # pragma: no cover
        ttl_ms = max(1, int((record.expires_at - time.time()) * 1000))
        self._client.set(
            f"idem:{key}",
            json.dumps(
                {
                    "request_hash": record.request_hash,
                    "response": record.response,
                    "expires_at": record.expires_at,
                    "in_progress": record.in_progress,
                }
            ),
            px=ttl_ms,
        )

    def put_if_absent(  # pragma: no cover - optional dependency
        self, key: str, record: StoredResponse
    ) -> Optional[StoredResponse]:
        """Atomic SET NX PX claim; returns existing record if already claimed."""
        ttl_ms = max(1, int((record.expires_at - time.time()) * 1000))
        claimed = self._client.set(
            f"idem:{key}",
            json.dumps(
                {
                    "request_hash": record.request_hash,
                    "response": record.response,
                    "expires_at": record.expires_at,
                    "in_progress": record.in_progress,
                }
            ),
            px=ttl_ms,
            nx=True,
        )
        if claimed:
            return None
        return self.get(key)

    def delete(self, key: str) -> None:  # pragma: no cover
        self._client.delete(f"idem:{key}")


class IdempotencyMiddleware:
    """Execute-once wrapper around money-moving handlers."""

    LOCAL_CACHE_MAX = 10000  # bound for the in-process replay cache

    def __init__(
        self,
        store: Optional[IdempotencyStore] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store or InMemoryIdempotencyStore(clock)
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self.executions = 0  # observability: how many real executions
        # Fast path: process-local cache of COMPLETED records in front of the
        # store (Redis seam included). Replays of a finished key never touch
        # the network. Only final (non-in_progress) records are cached, so
        # NX claim semantics are untouched: every first-execution still goes
        # through store.put_if_absent.
        self._local: Dict[str, StoredResponse] = {}
        self._local_lock = threading.Lock()
        self.local_hits = 0  # observability: replay fast-path hit rate

    def _local_get(self, key: str, digest: str) -> Optional[StoredResponse]:
        """Return a cached completed record iff it is live and hash-matches.

        A live cached record whose request_hash differs still proves the key
        was seen with another payload → caller raises 409 without a store
        round-trip.
        """
        with self._local_lock:
            rec = self._local.get(key)
            if rec is not None and rec.expires_at <= self._clock():
                del self._local[key]  # TTL expiry mirrors the store
                return None
            return rec

    def _local_put(self, key: str, record: StoredResponse) -> None:
        with self._local_lock:
            if len(self._local) >= self.LOCAL_CACHE_MAX:
                self._local.clear()  # bounded: worst case falls back to store
            self._local[key] = record

    def _local_delete(self, key: str) -> None:
        with self._local_lock:
            self._local.pop(key, None)

    def execute(
        self,
        key: str,
        payload: Any,
        fn: Callable[[], Any],
        *,
        ttl_seconds: Optional[int] = None,
    ) -> Any:
        if not key:
            raise ValueError("idempotency key is required for money movement")
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        digest = request_hash(payload)
        expires_at = self._clock() + ttl
        # Fast path: replay of a locally-cached COMPLETED record.
        cached = self._local_get(key, digest)
        if cached is not None:
            if cached.request_hash != digest:
                raise IdempotencyConflict(
                    f"idempotency key {key!r} replayed with a different payload"
                )
            self.local_hits += 1
            return cached.response  # replay: original response, no re-execute
        # Atomic claim (no check-then-act TOCTOU): exactly one concurrent
        # caller wins the claim and executes; losers see ConflictOrReplay.
        claim = StoredResponse(
            request_hash=digest, response=None,
            expires_at=expires_at, in_progress=True,
        )
        existing = self.store.put_if_absent(key, claim)
        if existing is not None:
            if existing.request_hash != digest:
                raise IdempotencyConflict(
                    f"idempotency key {key!r} replayed with a different payload"
                )
            if existing.in_progress:
                raise IdempotencyInProgress(
                    f"idempotency key {key!r} is being executed concurrently"
                )
            self._local_put(key, existing)  # warm the fast path for replays
            return existing.response  # replay: original response, no re-execute
        try:
            response = fn()
        except Exception:
            self.store.delete(key)  # failed execution releases the claim
            self._local_delete(key)
            raise
        self.executions += 1
        record = StoredResponse(
            request_hash=digest,
            response=response,
            expires_at=expires_at,
        )
        self.store.put(key, record)
        self._local_put(key, record)
        return response


def select_store(profile: Optional[str] = None) -> IdempotencyStore:
    profile = profile or os.environ.get("SOS_PROFILE", "dev")
    if os.environ.get("SOS_REDIS_URL"):
        return RedisIdempotencyStore()
    if profile == "production":
        raise AdapterUnavailableError(
            "production profile requires SOS_REDIS_URL for idempotency"
        )
    return InMemoryIdempotencyStore()
