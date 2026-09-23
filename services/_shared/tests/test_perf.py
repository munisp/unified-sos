"""Tests for _shared.perf: TTL cache, orjson fallback, timing/gzip wiring.

Perf-internal: no API contracts here — these assert the shared helpers
behave (cache hit avoids recompute, concurrent async computes share one
factory call, timing header present on instrumented responses).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import perf
from observability import instrument_fastapi


# -- TTLCache ---------------------------------------------------------------

def test_ttl_cache_hit_avoids_recompute():
    cache = perf.TTLCache(default_ttl=60.0)
    calls = []

    def factory():
        calls.append(1)
        return {"expensive": True}

    first = cache.get_or_create("k", factory)
    for _ in range(100):
        assert cache.get_or_create("k", factory) is first
    assert len(calls) == 1  # computed once, then served from cache


def test_ttl_cache_expires_and_invalidates():
    cache = perf.TTLCache(default_ttl=0.05)
    cache.set("a", 1)
    assert cache.get("a") == 1
    time.sleep(0.06)
    assert cache.get("a") is None  # expired lazily
    cache.set("b", 2)
    cache.invalidate("b")
    assert cache.get("b") is None


def test_ttl_cache_maxsize_evicts_oldest():
    cache = perf.TTLCache(default_ttl=60.0, maxsize=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert len(cache) == 2
    assert cache.get("c") == 3


def test_ttl_cache_async_stampede_protection():
    cache = perf.TTLCache(default_ttl=60.0)
    calls = []

    async def factory():
        calls.append(1)
        await asyncio.sleep(0.01)
        return 42

    async def run():
        return await asyncio.gather(
            *(cache.get_or_create_async("k", factory) for _ in range(25))
        )

    results = asyncio.run(run())
    assert results == [42] * 25
    assert len(calls) == 1  # one computation shared by all waiters


def test_ttl_cache_async_accepts_sync_factory():
    cache = perf.TTLCache(default_ttl=60.0)
    assert asyncio.run(cache.get_or_create_async("k", lambda: "v")) == "v"


# -- JSON serialization -----------------------------------------------------

def test_dumps_bytes_roundtrip_compact():
    payload = {"a": 1, "b": ["x", None], "when": "2024-01-01"}
    raw = perf.dumps_bytes(payload)
    assert isinstance(raw, bytes)
    assert json.loads(raw.decode()) == payload
    assert b" " not in raw  # compact separators either way


def test_orjson_response_class_renders_json():
    resp = perf.ORJSONResponse({"ok": True})
    assert resp.media_type == "application/json"
    assert json.loads(resp.body.decode()) == {"ok": True}


# -- instrument_fastapi wiring ----------------------------------------------

def _timed_app() -> FastAPI:
    app = FastAPI()

    @app.get("/big")
    def big():
        return {"payload": "x" * 4096}

    instrument_fastapi(app, "mod-perf-test")
    return app


def test_response_time_header_present():
    client = TestClient(_timed_app())
    resp = client.get("/big")
    assert resp.status_code == 200
    header = resp.headers.get(perf.RESPONSE_TIME_HEADER)
    assert header is not None and header.endswith("ms")
    assert float(header[:-2]) >= 0.0


def test_response_time_header_also_on_metrics():
    client = TestClient(_timed_app())
    resp = client.get("/metrics")
    assert resp.headers.get(perf.RESPONSE_TIME_HEADER, "").endswith("ms")


def test_gzip_engages_for_large_body_when_client_asks():
    client = TestClient(_timed_app())
    resp = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.json()["payload"] == "x" * 4096  # httpx transparently decodes


def test_gzip_opt_out(monkeypatch):
    monkeypatch.setenv("SOS_GZIP_ENABLED", "0")
    client = TestClient(_timed_app())
    resp = client.get("/big", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in resp.headers


def test_response_time_smoke_under_iterations():
    client = TestClient(_timed_app())
    start = time.perf_counter()
    for _ in range(200):
        assert client.get("/big").status_code == 200
    elapsed = time.perf_counter() - start
    # Generous ceiling: catches pathological per-request overhead, not
    # machine jitter (in-process TestClient, no network).
    assert elapsed < 10.0
