# Tests — Load, FAT/SAT & Acceptance Specs

Executable verification mapped to the procurement acceptance framework (`docs/procurement/acceptance-framework.md`). No milestone payment or revenue-share commencement without these gates passing.

| Suite | Maps To | Key Thresholds |
|---|---|---|
| [load/k6-ledger-split.js](load/k6-ledger-split.js) | Stage 2 (Performance/Stress) | 500,000 TPS TigerBeetle; 50,000 req/s APISIX; p99 < 50 ms split |
| [load/k6-apisix-gateway.js](load/k6-apisix-gateway.js) | Stage 2 (Performance/Stress) | 50,000 req/s APISIX sustained; p99 < 100 ms; zero errors |
| [load/sedona_joins.py](load/sedona_joins.py) | Sedona stress | 10,000 concurrent spatial joins without error (deterministic local profile; `--profile sedona` for real Spark/Sedona) |
| [contract/](contract/) — **delivered** | Stage 1 (FAT) | OpenAPI app↔contract drift gates (explicit expected-drift ledger); AsyncAPI envelope validation against in-memory event-bus payloads + registry schemas |
| [security/owasp_zap_baseline.sh](security/owasp_zap_baseline.sh) + [security/check_cves.py](security/check_cves.py) + [security/test_security_baseline.py](security/test_security_baseline.py) — **delivered** | Stage 3 (Pen-test) | OWASP Top 10 100% block; zero critical/high CVEs; read-model PII egress scan; tenant-isolation negative tests; inline-secret scan; USSD/IVR webhook auth fail-closed |
| [sat/run_sat.py](sat/run_sat.py) + [sat/test_sat_gates.py](sat/test_sat_gates.py) — **delivered** | Stage 4 (SAT) | Hardware integration + live bank clearing settlement; 10k-assessment zero-discrepancy reconciliation vs mod-rev-core; offline-POS replay (edge outbox → mod-market). Explicit SKIP locally, fail-closed under SAT_ENV=production |

Acceptance entrypoint: `make test-acceptance` (runs `tests/contract`, `tests/security`, `tests/sat`).

## Executable Gates (P0 Workstream E)

Single entrypoint: `python3 tests/gates/run_gates.py --gate stage1|stage2|stage3|sat|golive`
(or `make gates GATE=...`). Each gate writes JUnit XML + Markdown evidence to
`tests/evidence/<gate>-<timestamp>/` and exits non-zero on failure. Unmet live
dependencies are explicit `SKIPPED_NO_CREDENTIALS` / `SKIPPED_NO_TOOL` markers —
never silent passes; with `SOS_ENV=production` a missing dependency fails the gate.
CI wiring: `.github/workflows/gates.yml` (stage1 + contracts per PR; stage2/3/sat nightly/dispatch).

| Gate | Checks |
|---|---|
| `stage1` (FAT) | OpenAPI contract validation (tests/contract, else tests/contracts); per-service `pytest --cov --cov-fail-under=85` when pytest-cov available; trivy image scan over [gates/trivy-images.txt](gates/trivy-images.txt) |
| `stage2` | k6 ledger-split + APISIX gateway scripts (skip-gated without k6); deterministic 10k Sedona join harness |
| `stage3` | OWASP ZAP baseline per target (`SOS_ZAP_TARGETS`); zero critical/high CVE gate over trivy JSON (`SOS_TRIVY_JSON`) |
| `sat` (Stage 4) | Credential-free harness booting compose profiles ledger/payments/controlplane, integration suites when present, sha256-signed evidence bundle |
| `golive` (Stage 5) | [gates/go_live_checklist.py](gates/go_live_checklist.py) — artifact checks for the four gates below; exits non-zero unless all present |

## Go-Live Gates (Stage 5)

1. Data integrity & ledger balance — 100% legacy ↔ TigerBeetle reconciliation (Accountant-General + Lead Financial Auditor)
2. Security & NDPA clearance — zero unresolved High/Critical CVEs; Wazuh SOC active (State CISO)
3. Operational field readiness — ≥ 200 officers certified on POS/enforcement apps (IRS Chairman)
4. Final bank & commercial clearance — performance bond lodged; escrow configured; concession gazetted (AG + Commissioner for Finance)
