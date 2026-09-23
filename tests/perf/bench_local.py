#!/usr/bin/env python3
"""In-process micro-benchmark for SOS hot endpoints (no network, no Docker).

Times FastAPI TestClient calls on two representative hot paths:
  - citizen balance read : GET /citizen/v1/wallets/{wallet_id}  (mod-citizen-portal)
  - payment quote write  : POST /payments/v1/quotes             (mod-mobility-switch)

Asserts the in-process latency budget from
docs/operations/performance-budgets.md: p50 < 50ms per hot endpoint.

Run:  python3 tests/perf/bench_local.py
Deps: pip install -q pytest httpx fastapi pydantic prometheus-client \
        python-multipart pyjwt
"""

from __future__ import annotations

import os
import statistics
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient  # noqa: E402


def _import_service(service: str):
    """Import a service's `app` package in isolation.

    Every service ships a top-level `app` package, so purge any previously
    imported `app.*` modules before switching services.
    """
    svc_dir = os.path.join(REPO_ROOT, "services", service)
    for mod in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]
    if svc_dir in sys.path:
        sys.path.remove(svc_dir)
    sys.path.insert(0, svc_dir)
    import importlib

    return importlib.import_module("app.main")

ITERATIONS = int(os.environ.get("BENCH_ITERATIONS", "200"))
WARMUP = 20
P50_BUDGET_MS = float(os.environ.get("BENCH_P50_BUDGET_MS", "50"))


def bench(name: str, fn, iterations: int = ITERATIONS) -> dict:
    for _ in range(WARMUP):
        fn()
    samples_ms = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        fn()
        samples_ms.append((time.perf_counter_ns() - start) / 1e6)
    samples_ms.sort()
    result = {
        "name": name,
        "n": iterations,
        "p50_ms": statistics.median(samples_ms),
        "p95_ms": samples_ms[int(0.95 * len(samples_ms)) - 1],
        "p99_ms": samples_ms[int(0.99 * len(samples_ms)) - 1],
        "max_ms": samples_ms[-1],
    }
    print(
        f"{name:35s} n={result['n']:4d} "
        f"p50={result['p50_ms']:7.2f}ms p95={result['p95_ms']:7.2f}ms "
        f"p99={result['p99_ms']:7.2f}ms max={result['max_ms']:7.2f}ms"
    )
    return result


def bench_citizen_balance() -> dict:
    main_mod = _import_service("mod-citizen-portal")
    from app.repository import InMemoryCitizenPortalRepository
    from app.service import CitizenPortalService

    svc = CitizenPortalService(InMemoryCitizenPortalRepository())
    create_app = main_mod.create_app
    client = TestClient(create_app(svc.repo))
    wallet = svc.create_wallet("lagos", "12345678901")
    url = f"/citizen/v1/wallets/{wallet.wallet_id}"

    def call():
        resp = client.get(url, params={"state_id": "lagos"})
        assert resp.status_code == 200, resp.text

    return bench("citizen balance (GET wallet)", call)


def bench_payment_quote() -> dict:
    main_mod = _import_service("mod-mobility-switch")

    client = TestClient(main_mod.create_app())
    headers = {"X-State-Tenant": "lagos"}
    payload = {
        "bill_reference": "BILL-PERF-0001",
        "amount_kobo": 150_000,
        "payer": "bench-payer",
    }
    # First call creates the quote; subsequent calls hit the idempotent-replay
    # hot path (same inputs -> same deterministic quote_id).
    first = client.post("/payments/v1/quotes", json=payload, headers=headers)
    assert first.status_code == 201, first.text

    def call():
        resp = client.post("/payments/v1/quotes", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text

    return bench("payment quote (POST quotes)", call)


def main() -> int:
    results = [bench_citizen_balance(), bench_payment_quote()]
    failures = [r for r in results if r["p50_ms"] >= P50_BUDGET_MS]
    for r in failures:
        print(
            f"BUDGET FAIL: {r['name']} p50={r['p50_ms']:.2f}ms "
            f">= {P50_BUDGET_MS:.0f}ms budget",
            file=sys.stderr,
        )
    if failures:
        return 1
    print(f"OK: all hot endpoints within in-process p50 < {P50_BUDGET_MS:.0f}ms budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
