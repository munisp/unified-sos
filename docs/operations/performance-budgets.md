# Performance Budgets

Latency and footprint budgets for the SOS platform, plus how they are
measured and enforced. These budgets back the SLOs in `docs/operations/slo.md`
and the alerting rules in `deploy/observability/alerts-latency.yml`.

## Budget table

| Class | Scope | Budget | Alert / gate |
|---|---|---|---|
| Internal API (reads) | All module services (`mod-*`, `control-plane`) | p50 < 50 ms, p99 < 200 ms server-side | `SosInternalLatencyP50SLOBreach`, `SosInternalLatencyP99SLOBreach` |
| Money writes | Ledger/settlement paths (`mod-rev-core`, `mod-mobility-switch`, `mod-mortgage`, `mod-citizen-portal` wallet/settlement endpoints) | p99 < 300 ms end-to-end | `SosMoneyWriteLatencyP99SLOBreach` (page) |
| Availability | All services | 5xx < 1% sustained; fast-burn page at > 2%/5m | `SosHighErrorRate`, `SosErrorRateSLOBreachFast/Slow` |
| In-process hot endpoints | `GET /citizen/v1/wallets/{id}`, `POST /payments/v1/quotes` (idempotent replay) | p50 < 50 ms via TestClient | `tests/perf/bench_local.py` (exit 1 on breach) |
| Local stack health | Health endpoints of key services under `docker compose` | p95 < 500 ms | `tests/perf/smoke.js` thresholds |
| Citizen PWA | `apps/citizen-pwa` | TTI < 3 s on 3G (Moto G4 class), initial JS < 250 KB gzipped | Lighthouse CI budget on PRs touching `apps/citizen-pwa` |

## Measurement methodology

- **Server-side latency** comes from the
  `http_request_duration_seconds_bucket{service,le}` histogram scraped by
  Prometheus (`deploy/observability/prometheus.yml`, 15 s interval / 10 s
  timeout). Percentiles are pre-computed by the recording rules
  `sos:http_request_duration_seconds:p50/p99` over 5 m and 15 m windows; alerts
  and dashboards must use the recorded series, not ad-hoc quantiles.
- **Money-write vs internal classification** is by service name in
  `alerts-latency.yml` (regex list). When a new service gains a settlement or
  wallet-write endpoint, add it to the money-write regex in the same PR.
- **In-process benches** (`tests/perf/bench_local.py`) time FastAPI
  `TestClient` calls with `time.perf_counter_ns`, 20 warmup + 200 timed
  iterations (override with `BENCH_ITERATIONS`). They measure handler + domain
  cost only — no network, TLS, or gateway — so they catch algorithmic/ORM
  regressions early. They are a lower bound, not a substitute for the
  Prometheus SLOs.
- **Local stack smoke** (`tests/perf/smoke.js`) runs under k6 against the
  compose stack's health endpoints (5 VUs, 30 s). Threshold p95 < 500 ms is a
  coarse "is the dev stack healthy" gate, not a production SLO.
- **PWA budgets** are measured with Lighthouse CI on a throttled 3G profile;
  JS budget is computed on the gzipped production bundle.

## Regression gates

1. **Every PR**: `python3 tests/perf/bench_local.py` must exit 0
   (p50 < 50 ms per hot endpoint). Install deps with
   `pip install -q pytest httpx fastapi pydantic prometheus-client python-multipart pyjwt`.
2. **Compose changes**: validate with
   `python3 -c 'import yaml;yaml.safe_load(open("deploy/docker-compose.yml"))'`
   and (where Docker is available) `docker compose -f deploy/docker-compose.yml config -q`.
3. **Observability changes**: alerts must load — `promtool check rules
   deploy/observability/alerts*.yml` (or at minimum a YAML parse) in CI.
4. **Release candidates**: run `k6 run tests/perf/smoke.js` against a local
   stack, and confirm no `SosInternalLatencyP99SLOBreach` /
   `SosMoneyWriteLatencyP99SLOBreach` alerts fired in staging over the
   previous 24 h.
5. A budget breach is treated like a test failure: either fix the regression
   or amend this file in the same PR with an explicit rationale.
