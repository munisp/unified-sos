# Tests — Load, FAT/SAT & Acceptance Specs

Executable verification mapped to the procurement acceptance framework (`docs/procurement/acceptance-framework.md`). No milestone payment or revenue-share commencement without these gates passing.

| Suite | Maps To | Key Thresholds |
|---|---|---|
| [load/k6-ledger-split.js](load/k6-ledger-split.js) | Stage 2 (Performance/Stress) | 500,000 TPS TigerBeetle; 50,000 req/s APISIX; p99 < 50 ms split |
| `load/` (planned) | Sedona stress | 10,000 concurrent spatial joins without error |
| [contract/](contract/) — **delivered** | Stage 1 (FAT) | OpenAPI app↔contract drift gates (explicit expected-drift ledger, no silent skips); AsyncAPI envelope validation against in-memory event-bus payloads + generated registry schemas |
| [security/](security/) — **delivered** | Stage 3 (Pen-test) | Blocking local gates: read-model PII egress scan (NIN/BVN/MSISDN), tenant-isolation negative tests (citizen-portal/ppp/police-cad), inline-secret scan of `deploy/**` + `infra/**`, USSD/IVR webhook auth (fail-closed) |
| [sat/](sat/) — **delivered** | Stage 4 (SAT) | Scripted gates emitting per-gate JUnit XML: 10k-assessment zero-discrepancy reconciliation vs mod-rev-core in-memory ledger; offline-POS replay (edge-daemon outbox → mod-market sync). Explicit SKIP locally, fail-closed under `SAT_ENV=production` |

Acceptance entrypoint: `make test-acceptance` (runs `tests/contract`, `tests/security`, `tests/sat`).

## Go-Live Gates (Stage 5)

1. Data integrity & ledger balance — 100% legacy ↔ TigerBeetle reconciliation (Accountant-General + Lead Financial Auditor)
2. Security & NDPA clearance — zero unresolved High/Critical CVEs; Wazuh SOC active (State CISO)
3. Operational field readiness — ≥ 200 officers certified on POS/enforcement apps (IRS Chairman)
4. Final bank & commercial clearance — performance bond lodged; escrow configured; concession gazetted (AG + Commissioner for Finance)
