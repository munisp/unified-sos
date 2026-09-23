"""Shared performance primitives for SOS FastAPI services.

Zero new hard dependencies: every facility degrades gracefully when its
optional accelerator is absent.

* :func:`dumps_bytes` / :data:`ORJSONResponse` — fast JSON serialization.
  Uses ``orjson`` when installed; otherwise falls back to the stdlib
  ``json`` encoder (compact separators) via a ``JSONResponse`` subclass, so
  callers can set ``default_response_class=ORJSONResponse`` unconditionally.
* :func:`add_gzip_middleware` — one-line wiring of Starlette's
  ``GZipMiddleware`` (stdlib Starlette; only compresses when the client
  advertises ``Accept-Encoding: gzip``).
* :func:`format_response_time_ms` / :data:`RESPONSE_TIME_HEADER` — the
  ``X-Response-Time`` request-timing header wired by
  ``_shared.observability.instrument_fastapi``.
* :class:`TTLCache` — in-process TTL cache for static-ish lookups (config
  maps, role tables, rendered catalogs). Synchronous reads are guarded by a
  ``threading.Lock``; :meth:`TTLCache.get_or_create_async` additionally uses
  ``asyncio`` locks so concurrent coroutines compute a missing value exactly
  once (cache-stampede protection) without blocking the event loop.

Import convention (same as ``_shared.hashchain``): services insert the
``services/`` directory into ``sys.path`` before importing.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from typing import Any, Callable, Dict, Hashable, Optional, Tuple, Union

#: Response header carrying server-side request latency.
RESPONSE_TIME_HEADER = "X-Response-Time"

try:  # optional accelerator; never a hard dependency
    import orjson as _orjson
except ImportError:  # pragma: no cover - exercised when orjson absent
    _orjson = None  # type: ignore[assignment]

ORJSON_AVAILABLE = _orjson is not None


def dumps_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to JSON bytes, using orjson when available.

    The stdlib fallback uses compact separators and ``default=str`` so it
    accepts the same datetime/enum payloads Starlette responses commonly
    carry; output is UTF-8 JSON either way.
    """
    if _orjson is not None:
        return _orjson.dumps(obj)
    import json

    return json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


def _build_response_class():
    """Pick the fastest available JSON response class."""
    from starlette.responses import JSONResponse

    if _orjson is not None:
        try:
            from fastapi.responses import ORJSONResponse

            return ORJSONResponse
        except ImportError:  # pragma: no cover - starlette without fastapi
            pass

    class _CompactJSONResponse(JSONResponse):
        """Drop-in ORJSONResponse fallback on the stdlib encoder."""

        def render(self, content: Any) -> bytes:
            return dumps_bytes(content)

    _CompactJSONResponse.__name__ = "ORJSONResponse"
    return _CompactJSONResponse


#: Default JSON response class: fastapi's ``ORJSONResponse`` when orjson is
#: installed, otherwise an equivalent compact stdlib-backed ``JSONResponse``.
ORJSONResponse = _build_response_class()


def format_response_time_ms(elapsed_seconds: float) -> str:
    """Render an elapsed duration for the ``X-Response-Time`` header."""
    return f"{elapsed_seconds * 1000.0:.2f}ms"


def add_gzip_middleware(app: Any, minimum_size: int = 1024) -> None:
    """Attach Starlette's GZipMiddleware (compresses only for gzip clients).

    ``minimum_size`` keeps small payloads (USSD/IVR ``CON``/``END`` texts,
    health probes) uncompressed where the framing overhead would exceed the
    savings.
    """
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=minimum_size)


class TTLCache:
    """In-process TTL cache with asyncio stampede protection.

    Suitable for static-ish per-process lookups: parsed config files, role
    maps, seeded catalogs. Entries expire lazily on access; ``maxsize``
    bounds memory by evicting the oldest entries. Thread-safe for sync
    callers; async ``get_or_create_async`` holds no lock while the factory
    runs, and concurrent waiters share one computation per key.
    """

    def __init__(self, default_ttl: float = 60.0, maxsize: int = 1024) -> None:
        if default_ttl <= 0:
            raise ValueError("default_ttl must be positive")
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.default_ttl = float(default_ttl)
        self.maxsize = int(maxsize)
        self._lock = threading.Lock()
        # key -> (value, expires_at_monotonic)
        self._entries: Dict[Hashable, Tuple[Any, float]] = {}
        self._async_locks: Dict[Hashable, asyncio.Lock] = {}

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Return the cached value, or ``default`` when missing/expired."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return default
            value, expires_at = entry
            if self._now() >= expires_at:
                del self._entries[key]
                return default
            return value

    def set(self, key: Hashable, value: Any, ttl: Optional[float] = None) -> Any:
        """Store ``value`` under ``key`` with ``ttl`` seconds (default TTL)."""
        expires_at = self._now() + (self.default_ttl if ttl is None else float(ttl))
        with self._lock:
            if len(self._entries) >= self.maxsize and key not in self._entries:
                oldest = min(self._entries.items(), key=lambda kv: kv[1][1])[0]
                del self._entries[oldest]
            self._entries[key] = (value, expires_at)
        return value

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_create(
        self,
        key: Hashable,
        factory: Callable[[], Any],
        ttl: Optional[float] = None,
    ) -> Any:
        """Sync cache-aside: one thread computes; others may compute too
        (the last store wins) — use the async variant where stampede
        protection matters."""
        sentinel = object()
        value = self.get(key, sentinel)
        if value is not sentinel:
            return value
        return self.set(key, factory(), ttl)

    async def get_or_create_async(
        self,
        key: Hashable,
        factory: Callable[[], Union[Any, Awaitable[Any]]],
        ttl: Optional[float] = None,
    ) -> Any:
        """Async cache-aside with per-key ``asyncio.Lock``.

        Concurrent coroutines awaiting the same missing key share exactly
        one ``factory()`` invocation; the factory (sync or async) runs
        outside the entry lock so the event loop stays unblocked.
        """
        sentinel = object()
        value = self.get(key, sentinel)
        if value is not sentinel:
            return value
        lock = self._async_locks.get(key)
        if lock is None:
            lock = self._async_locks.setdefault(key, asyncio.Lock())
        async with lock:
            value = self.get(key, sentinel)  # re-check: another waiter filled it
            if value is not sentinel:
                return value
            computed = factory()
            if inspect.isawaitable(computed):
                computed = await computed
            result = self.set(key, computed, ttl)
        # Idle locks are cheap but unbounded; drop once drained.
        self._async_locks.pop(key, None)
        return result


__all__ = [
    "ORJSON_AVAILABLE",
    "ORJSONResponse",
    "RESPONSE_TIME_HEADER",
    "TTLCache",
    "add_gzip_middleware",
    "dumps_bytes",
    "format_response_time_ms",
]
