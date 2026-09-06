"""Lightweight, dependency-optional observability instrumentation (Stage 7.C).

``instrument_fastapi(app, service_name)`` wires three things onto any SOS
FastAPI service with a single call:

1. ``/metrics`` — Prometheus text-exposition endpoint backed by a tiny
   zero-dependency local registry (per-app instance, so tests are
   deterministic). Exposes ``http_requests_total``,
   ``http_request_duration_seconds`` (histogram) and ``http_errors_total``
   labelled by service/route/method, plus ``service_info``.
2. Optional OpenTelemetry wiring — only when the ``opentelemetry-*``
   packages are installed *and* ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.
   Every step is import-guarded and fail-soft: any missing package or
   exporter error falls back to the local registry only.
3. ``X-Request-ID`` propagation middleware — echoes an inbound request ID
   or mints a new one, exposes it on ``request.state.request_id`` and sets
   the response header.

Import convention (same as ``_shared.hashchain``): services insert the
``services/`` directory into ``sys.path`` before importing. Container
images that only ship ``app/`` guard the import and skip instrumentation.
"""
from __future__ import annotations

import os
import time
import uuid
from threading import Lock
from typing import Dict, Iterable, Optional, Tuple

HISTOGRAM_BUCKETS: Tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

_LABEL_BLACKLIST_PATHS = frozenset({"/metrics"})


class LocalRegistry:
    """Minimal in-process metric registry (counters + fixed-bucket histograms).

    Pure stdlib, thread-safe, and entirely deterministic — no wall-clock or
    global state leaks between app instances because each ``instrument_fastapi``
    call creates its own registry.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        # {(metric, labels-tuple): value}
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
        # {(metric, labels-tuple): [bucket_counts..., +Inf], sum, count}
        self._histograms: Dict[
            Tuple[str, Tuple[Tuple[str, str], ...]],
            list,
        ] = {}

    @staticmethod
    def _key(metric: str, labels: Optional[Dict[str, str]]) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
        return metric, tuple(sorted((labels or {}).items()))

    def inc(self, metric: str, labels: Optional[Dict[str, str]] = None, value: float = 1.0) -> None:
        with self._lock:
            key = self._key(metric, labels)
            self._counters[key] = self._counters.get(key, 0.0) + value

    def observe(self, metric: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        with self._lock:
            key = self._key(metric, labels)
            entry = self._histograms.get(key)
            if entry is None:
                entry = [[0] * (len(HISTOGRAM_BUCKETS) + 1), 0.0, 0]
                self._histograms[key] = entry
            buckets, total, count = entry
            for i, bound in enumerate(HISTOGRAM_BUCKETS):
                if value <= bound:
                    buckets[i] += 1
            buckets[-1] += 1  # +Inf
            entry[1] = total + value
            entry[2] = count + 1

    # -- exposition -----------------------------------------------------

    @staticmethod
    def _fmt_labels(labels: Tuple[Tuple[str, str], ...], extra: Optional[Dict[str, str]] = None) -> str:
        merged = list(labels)
        if extra:
            merged.extend(sorted(extra.items()))
        if not merged:
            return ""
        inner = ",".join(f'{k}="{v}"' for k, v in merged)
        return "{" + inner + "}"

    @staticmethod
    def _num(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else repr(value)

    def render_prometheus(self, service_name: str) -> str:
        """Render the registry in Prometheus text exposition format."""
        lines = [
            "# HELP service_info Static service identity gauge (always 1).",
            "# TYPE service_info gauge",
            f'service_info{{service="{service_name}",version="0.1.0"}} 1',
            "# HELP http_requests_total Total HTTP requests by route/method/status.",
            "# TYPE http_requests_total counter",
        ]
        with self._lock:
            counters = dict(self._counters)
            histograms = {k: [list(v[0]), v[1], v[2]] for k, v in self._histograms.items()}
        for (metric, labels), value in sorted(counters.items()):
            if metric.startswith("http_"):
                lines.append(f"{metric}{self._fmt_labels(labels)} {self._num(value)}")
        lines += [
            "# HELP http_errors_total HTTP 5xx responses by route/method.",
            "# TYPE http_errors_total counter",
            "# HELP http_request_duration_seconds Request latency histogram.",
            "# TYPE http_request_duration_seconds histogram",
        ]
        for (metric, labels), (buckets, total, count) in sorted(histograms.items()):
            cumulative = 0
            for i, bound in enumerate(HISTOGRAM_BUCKETS):
                cumulative += buckets[i]
                lines.append(
                    f"{metric}_bucket{self._fmt_labels(labels, {'le': self._num(bound)})} {cumulative}"
                )
            cumulative += buckets[-1]
            lines.append(
                f'{metric}_bucket{self._fmt_labels(labels, {"le": "+Inf"})} {cumulative}'
            )
            lines.append(f"{metric}_sum{self._fmt_labels(labels)} {self._num(round(total, 6))}")
            lines.append(f"{metric}_count{self._fmt_labels(labels)} {count}")
        return "\n".join(lines) + "\n"


def _route_label(request) -> str:
    """Best-effort low-cardinality route label (path template if routed)."""
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    return request.url.path


def _try_setup_otel(app, service_name: str) -> bool:
    """Wire OpenTelemetry tracing+metrics when available and configured.

    Fail-soft by design: missing packages, unset endpoint, or any exporter
    error returns False and the local registry remains the only sink.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return False
    try:
        from opentelemetry import metrics as otel_metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False
    try:
        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(tracer_provider)
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
        otel_metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
        FastAPIInstrumentor.instrument_app(app)
        return True
    except Exception:
        return False


def instrument_fastapi(app, service_name: str, registry: Optional[LocalRegistry] = None) -> None:
    """Attach /metrics, request-ID propagation, and optional OTel to ``app``.

    Idempotent per app instance: repeated calls with an explicit ``registry``
    re-use it; otherwise a fresh registry is created once and stored on
    ``app.state.observability_registry``.
    """
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    registry = registry or getattr(app.state, "observability_registry", None) or LocalRegistry()
    app.state.observability_registry = registry
    app.state.otel_enabled = _try_setup_otel(app, service_name)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        if request.url.path in _LABEL_BLACKLIST_PATHS:
            return await call_next(request)
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        labels = {
            "service": service_name,
            "route": _route_label(request),
            "method": request.method,
            "status": str(response.status_code),
        }
        registry.inc("http_requests_total", labels)
        registry.observe(
            "http_request_duration_seconds",
            elapsed,
            {k: v for k, v in labels.items() if k != "status"},
        )
        if response.status_code >= 500:
            registry.inc(
                "http_errors_total",
                {k: v for k, v in labels.items() if k != "status"},
            )
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(
            registry.render_prometheus(service_name),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


def exposition_lines(registry: LocalRegistry, service_name: str) -> Iterable[str]:
    """Test helper: iterate rendered exposition lines."""
    return iter(registry.render_prometheus(service_name).splitlines())
