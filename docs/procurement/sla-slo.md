# Operational SLA, SLO & Service Credit Penalty Framework

## System Availability & Performance SLOs

| Platform Component | Availability | Latency (p95 / p99) | Max Planned Downtime | RPO / RTO |
|---|---|---|---|---|
| TigerBeetle Ledger Kernel | 99.999% (five 9s) | < 2 ms / < 5 ms | Zero (rolling upgrades) | RPO=0 / RTO<10 s |
| Mojaloop Payment Switch | 99.99% | < 25 ms / < 50 ms | < 15 min/quarter | RPO<1 s / RTO<1 m |
| APISIX API Gateway & WAF | 99.99% | < 5 ms / < 15 ms | < 15 min/quarter | RPO=0 / RTO<30 s |
| Cadastral GIS (PostGIS/Sedona) | 99.95% | < 200 ms / < 500 ms | < 1 h/month | RPO<1 m / RTO<5 m |
| Lakehouse & AI Analytics | 99.90% | batch < 15 min / stream < 5 s | < 2 h/month | RPO<5 m / RTO<30 m |
| Field Mobile POS Sync | 99.90% (offline-first) | sync < 3 s upon connect | < 2 h/month | RPO<local / RTO<1 m |

## Incident Severity, MTTR & Service Credits

| Severity | Impact | MTTA | MTTR | Service Credit Penalty |
|---|---|---|---|---|
| **Sev-1 (Critical)** | Total outage of payment switch, ledger kernel, or API gateway halting revenue collection statewide | < 15 min | < 2 h | 5% of monthly concession fee per hour of delay past MTTR |
| **Sev-2 (High)** | Major module failure (land registry offline, WIM ingest stopped) with no workaround | < 30 min | < 4 h | 2% of monthly concession fee per 2 hours of delay |
| **Sev-3 (Medium)** | Degraded performance, non-critical dashboard latency, partial POS sync delays | < 2 h | < 12 h | 0.5% of monthly concession fee per business day |
| **Sev-4 (Low)** | Cosmetic UI bugs, minor documentation errors, enhancement requests | < 8 h | Next release cycle | None |

## Vulnerability Remediation SLAs (CVSS v3.1)

| Score | Classification | Remediation SLA | Non-Compliance Penalty |
|---|---|---|---|
| 9.0–10.0 | Critical | 24 h hotfix | ₦500,000 per calendar day of delay |
| 7.0–8.9 | High | 72 h patch | ₦250,000 per calendar day of delay |
| 4.0–6.9 | Medium | 14 business days | ₦50,000 per business day of delay |
| 0.1–3.9 | Low | Next sprint release | Recorded in vendor performance scorecard |
