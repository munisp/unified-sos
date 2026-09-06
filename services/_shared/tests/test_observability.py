"""Observability instrumentation tests (Stage 7.C).

Covers: Prometheus exposition format, counter/histogram increments,
request-ID propagation, and the OTel-absent fallback to the local registry.
Deterministic: each test builds a fresh app + registry.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from observability import LocalRegistry, instrument_fastapi


def make_app(service_name: str = "mod-test") -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    def get_item(item_id: str, request: Request):
        return {"item_id": item_id, "request_id": request.state.request_id}

    @app.get("/boom")
    def boom():
        raise HTTPException(500, "kaboom")

    instrument_fastapi(app, service_name)
    return app


def test_metrics_endpoint_format():
    client = TestClient(make_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert '# TYPE http_requests_total counter' in body
    assert '# TYPE http_request_duration_seconds histogram' in body
    assert '# TYPE http_errors_total counter' in body
    assert 'service_info{service="mod-test",version="0.1.0"} 1' in body


def test_counters_increment_per_route():
    client = TestClient(make_app())
    assert client.get("/items/abc").status_code == 200
    assert client.get("/items/abc").status_code == 200
    assert client.get("/items/def").status_code == 200
    body = client.get("/metrics").text
    assert 'http_requests_total{method="GET",route="/items/{item_id}",service="mod-test",status="200"} 3' in body
    assert 'http_request_duration_seconds_count{method="GET",route="/items/{item_id}",service="mod-test"} 3' in body
    assert 'http_request_duration_seconds_bucket{method="GET",route="/items/{item_id}",service="mod-test",le="+Inf"} 3' in body
    assert 'http_request_duration_seconds_sum{method="GET",route="/items/{item_id}",service="mod-test"}' in body


def test_error_counter_increments_on_5xx():
    client = TestClient(make_app(), raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500
    body = client.get("/metrics").text
    assert 'http_errors_total{method="GET",route="/boom",service="mod-test"} 1' in body
    assert 'http_requests_total{method="GET",route="/boom",service="mod-test",status="500"} 1' in body


def test_metrics_route_not_self_instrumented():
    client = TestClient(make_app())
    client.get("/metrics")
    body = client.get("/metrics").text
    assert 'route="/metrics"' not in body


def test_request_id_echoed_and_propagated():
    client = TestClient(make_app())
    resp = client.get("/items/abc", headers={"X-Request-ID": "req-123"})
    assert resp.headers["X-Request-ID"] == "req-123"
    assert resp.json()["request_id"] == "req-123"


def test_request_id_generated_when_absent():
    client = TestClient(make_app())
    resp = client.get("/items/abc")
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) == 32  # uuid4 hex
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


def test_otel_absent_falls_back_to_local_registry(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    app = make_app()
    # opentelemetry-* is not installed in the test env → fail-soft.
    assert app.state.otel_enabled is False
    client = TestClient(app)
    client.get("/items/abc")
    assert "http_requests_total" in client.get("/metrics").text


def test_otel_no_endpoint_skips_wiring(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = make_app()
    assert app.state.otel_enabled is False


def test_registries_isolated_between_apps():
    app_a, app_b = make_app("mod-a"), make_app("mod-b")
    TestClient(app_a).get("/items/1")
    body_b = TestClient(app_b).get("/metrics").text
    assert "http_requests_total{" not in body_b  # HELP/TYPE lines only, no samples


def test_local_registry_render_is_deterministic():
    reg = LocalRegistry()
    reg.inc("http_requests_total", {"route": "/x", "method": "GET", "status": "200"})
    reg.observe("http_request_duration_seconds", 0.03, {"route": "/x", "method": "GET"})
    out1 = reg.render_prometheus("svc")
    out2 = reg.render_prometheus("svc")
    assert out1 == out2
    assert out1.endswith("\n")
