# SOS Platform Service Level Objectives (Stage 7.C)

This document defines the per-tier SLOs, error budgets, and the metrics that
back them. Metrics are emitted by every service at `/metrics`
(`services/_shared/observability.py` for FastAPI services;
`internal/observability` for the Go services) and scraped by Prometheus per
`deploy/observability/prometheus.yml`. Alerts live in
`deploy/observability/alerts.yml`.

## Service tiers

| Tier | Services | Rationale |
|------|----------|-----------|
| **Tier 0 — money & trust** | mod-rev-core, ledger (TigerBeetle), mod-identity | Revenue settlement, double-entry ledger, resident identity/verification; corruption or downtime directly blocks state revenue or leaks PII risk. |
| **Tier 1 — citizen-facing** | mod-citizen-portal, mod-transparency, mod-health, mod-education, mod-police-cad, mod-geospatial-gateway, mod-geospatial, mod-gis-lands, mod-gis-luc | Citizen-visible journeys; degradation is public and urgent but not financially irreversible. |
| **Tier 2 — back office** | mod-agri-waybill, mod-environment, mod-forestry, mod-market, mod-mining, mod-mobility-switch, mod-ppp-investment, mod-transport-wim, mod-kyc-kyb, control-plane | Operator/back-office workloads; short degradation is tolerable. |
| **Tier 3 — batch** | lakehouse, edge-daemon sync | Batch/async; SLOs expressed as job freshness rather than request latency. |

## SLO targets

| Tier | Availability (successful responses / total, 30d rolling) | Latency | Error budget (30d) |
|------|----------------------------------------------------------|---------|--------------------|
| Tier 0 | **99.95%** | p95 ≤ 300ms, p99 ≤ 800ms | ~21.6 min downtime-equiv |
| Tier 1 | **99.9%** | p95 ≤ 400ms, p99 ≤ 800ms | ~43.2 min |
| Tier 2 | **99.5%** | p95 ≤ 800ms, p99 ≤ 2s | ~3.6 h |
| Tier 3 | **99.0%** (job success) | bronze→silver freshness ≤ 1h, silver→gold ≤ 24h | ~7.2 h |

### Measurement

* **Availability** — `1 - sum(rate(http_errors_total[30d])) / sum(rate(http_requests_total[30d]))`
  per `service` label (5xx only; 4xx are client errors and do not consume the
  budget).
* **Latency** — `histogram_quantile()` over
  `http_request_duration_seconds_bucket` per service.
* **Business invariants** (Tier 0, alerting-only, zero budget):
  * `audit_chain_verify_failures_total` — any hash-chain verification failure
    is a **critical** page (audit immutability breach).
  * `|ledger_debits_total - ledger_credits_total| > 0` — double-entry
    imbalance is a **critical** page; freeze settlements before investigating.

## Error-budget policy

* Budget > 25% remaining: normal release cadence.
* Budget < 25% remaining: feature freeze for the affected tier; only
  reliability fixes ship.
* Budget exhausted: incident review within 48h; rollback playbooks exercised
  before the next release train.

## Burn-rate alerts

The alerts in `deploy/observability/alerts.yml` implement a pragmatic subset:
a 1%-of-traffic 5xx ratio sustained for 10m (fast burn proxy), p99 > 800ms
for 15m, and scrape-down for 5m. Multi-window burn-rate alerts
(14.4×/6×/3×/1× over 1h/6h/3d windows, per the Google SRE workbook) are the
recommended next iteration once Prometheus retention exceeds 30d.

## Request correlation

All services propagate `X-Request-ID` (inbound header echoed, otherwise a
uuid4 is minted and returned on the response). When
`OTEL_EXPORTER_OTLP_ENDPOINT` is set and the `opentelemetry-*` packages are
installed, FastAPI services additionally emit OTLP traces/metrics with
`service.name` set to the module name; without them they fail soft to the
local Prometheus registry only.
