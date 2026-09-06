# Tests — Load, FAT/SAT & Acceptance Specs

Executable verification mapped to the procurement acceptance framework (`docs/procurement/acceptance-framework.md`). No milestone payment or revenue-share commencement without these gates passing.

| Suite | Maps To | Key Thresholds |
|---|---|---|
| [load/k6-ledger-split.js](load/k6-ledger-split.js) | Stage 2 (Performance/Stress) | 500,000 TPS TigerBeetle; 50,000 req/s APISIX; p99 < 50 ms split |
| `load/` (planned) | Sedona stress | 10,000 concurrent spatial joins without error |
| `contract/` (planned) | Stage 1 (FAT) | OpenAPI schema validation; Dapr bindings; > 85% coverage |
| `security/` (planned) | Stage 3 (Pen-test) | OWASP Top 10 100% block; zero critical/high CVEs |
| `sat/` (planned) | Stage 4 (SAT) | Hardware integration + live bank clearing settlement |

## Go-Live Gates (Stage 5)

1. Data integrity & ledger balance — 100% legacy ↔ TigerBeetle reconciliation (Accountant-General + Lead Financial Auditor)
2. Security & NDPA clearance — zero unresolved High/Critical CVEs; Wazuh SOC active (State CISO)
3. Operational field readiness — ≥ 200 officers certified on POS/enforcement apps (IRS Chairman)
4. Final bank & commercial clearance — performance bond lodged; escrow configured; concession gazetted (AG + Commissioner for Finance)
