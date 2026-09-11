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
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol

DEFAULT_TTL_SECONDS = 24 * 3600


class AdapterUnavailableError(RuntimeError):
    pass


class IdempotencyConflict(RuntimeError):
    """Same idempotency key reused with a different payload (HTTP 409)."""

    status_code = 409


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


class IdempotencyStore(Protocol):
    def get(self, key: str) -> Optional[StoredResponse]: ...

    def put(self, key: str, record: StoredResponse) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._data: Dict[str, StoredResponse] = {}
        self._clock = clock

    def get(self, key: str) -> Optional[StoredResponse]:
        rec = self._data.get(key)
        if rec is not None and rec.expires_at <= self._clock():
            del self._data[key]  # TTL expiry
            return None
        return rec

    def put(self, key: str, record: StoredResponse) -> None:
        self._data[key] = record


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
        return StoredResponse(data["request_hash"], data["response"], data["expires_at"])

    def put(self, key: str, record: StoredResponse) -> None:  # pragma: no cover
        ttl_ms = max(1, int((record.expires_at - time.time()) * 1000))
        self._client.set(
            f"idem:{key}",
            json.dumps(
                {
                    "request_hash": record.request_hash,
                    "response": record.response,
                    "expires_at": record.expires_at,
                }
            ),
            px=ttl_ms,
        )


class IdempotencyMiddleware:
    """Execute-once wrapper around money-moving handlers."""

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

    def execute(self, key: str, payload: Any, fn: Callable[[], Any]) -> Any:
        if not key:
            raise ValueError("idempotency key is required for money movement")
        digest = request_hash(payload)
        existing = self.store.get(key)
        if existing is not None:
            if existing.request_hash != digest:
                raise IdempotencyConflict(
                    f"idempotency key {key!r} replayed with a different payload"
                )
            return existing.response  # replay: original response, no re-execute
        response = fn()
        self.executions += 1
        self.store.put(
            key,
            StoredResponse(
                request_hash=digest,
                response=response,
                expires_at=self._clock() + self.ttl_seconds,
            ),
        )
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
