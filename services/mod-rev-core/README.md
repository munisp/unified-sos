# mod-rev-core — Core Revenue & Automated Assessment Engine

**WP-05 / EPIC-05 · Lot 3 · RT-02**

Digitizes direct assessment, PAYE, withholding tax, consumption tax, and informal market daily levies across all state LGAs. Generates STINs linked to NIN/BVN/CAC; issues dynamic NIBSS e-Bills and QR codes; posts every kobo double-entry to TigerBeetle.

- **API contract:** [`contracts/openapi/revenue-assessments.yaml`](../../contracts/openapi/revenue-assessments.yaml)
- **Schema:** [`db/migrations/0002_revenue_core.sql`](../../db/migrations/0002_revenue_core.sql)
- **Config surface:** JSON policy files — tax brackets, reliefs, penalty rates, revenue heads (`config/states/<state>/`); split policy packs under `internal/revenue/seed/` (gazetted defaults) or `REV_CORE_POLICY_DIR`
- **NFRs:** 99.999% availability; < 25 ms p99; 2,500 bill settlements/sec per state at peak
- **Acceptance:** 100,000 automated assessments with zero discrepancy vs gazetted tax laws; offline POS caches/signs 5,000 transactions
- **Stack:** Go · PostgreSQL (RLS) · Temporal · Redis · TigerBeetle

## Running

```sh
cd services/mod-rev-core
go run ./cmd/server          # listens on :8080
go vet ./... && go test ./...
```

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `REV_CORE_ADDR` | `:8080` | HTTP listen address |
| `REV_CORE_POLICY_DIR` | _(embedded seeds)_ | Directory of revenue-split policy packs overriding the embedded gazetted seeds |
| `REV_CORE_LEDGER` | `memory` | Ledger backend: `memory` (in-memory fake, no cluster needed) or `tigerbeetle` (production adapter — documented stub pending cluster provisioning, see `ledger/README.md`) |
| `TB_ADDRESSES` | — | TigerBeetle cluster addresses for the production adapter |

Bearer JWT validation (Keycloak realm, tenant claims) is enforced by the APISIX gateway upstream; this service trusts the `{state_id}` path tenant after gateway authentication.

## Endpoints (per OpenAPI contract)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/states/{state_id}/revenue/assessments` | Create assessment & issue bill (201). Honours `Idempotency-Key`: same key + payload → 200 replay of the same bill; same key + different payload → 409 |
| `GET` | `/api/v1/states/{state_id}/revenue/assessments/{assessment_id}` | Fetch one assessment (404 across tenant boundaries) |
| `POST` | `/api/v1/states/{state_id}/revenue/payments/webhook` | Clearing-switch settlement webhook: marks the bill PAID and executes the gazetted statutory split as an atomic TigerBeetle linked-transfer chain |
| `GET` | `/healthz` | Liveness probe |

`{state_id}` ∈ `{lagos, ogun, osun, benue, nasarawa, taraba}`.

### Example

```sh
curl -X POST localhost:8080/api/v1/states/nasarawa/revenue/assessments \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: req-001' \
  -d '{"taxpayer_stin":"NG-NAS-2026-892104","mda_code":"MDA-BIR-001",
       "revenue_head":"REV_DIRECT_ASSESSMENT","tax_period_year":2026,
       "gross_income_kobo":1200000000,"allowable_deductions_kobo":240000000,
       "calculated_tax_kobo":192000000}'

curl -X POST localhost:8080/api/v1/states/nasarawa/revenue/payments/webhook \
  -H 'Content-Type: application/json' \
  -d '{"bill_reference":"BILL-…","amount_kobo":192000000,"channel":"NIP",
       "provider_reference":"NIBSS-…"}'
```

## Domain design

- **STIN registry** — `NG-{STATE3}-{YEAR}-{6 digits}` (e.g. `NG-NAS-2026-892104`); the state segment must match the path tenant (cross-tenant STINs are rejected). Taxpayers are registered on first assessment.
- **Revenue-head catalog** — loaded from gazetted policy packs (`revenue-split.schema.json`) via `ledger/splits`; unknown heads are 400s. No tax policy is hard-coded (CONTRIBUTING.md rule 1).
- **Assessment calculation** — validates `calculated_tax_kobo ≤ gross_income_kobo − allowable_deductions_kobo` (amount mismatch → 400).
- **Multi-tenancy** — the store is partitioned per state behind the `Store` interface (in-memory default). The production adapter targets PostgreSQL with RLS per `db/migrations/0002_revenue_core.sql` (documented stub: implement `Store` against `revenue.taxpayers`/`revenue.assessments`; balances never live in SQL — ADR-002).
- **Settlement** — funds land in the payer clearing account (class 1001), then `ledger/splits` computes the gazetted split (deterministic kobo rounding; INSTANT legs only; END_OF_MONTH legs swept later) and submits it as a **linked atomic chain** — all legs commit or none do (Clause 22.2).

## WP-05 acceptance mapping

| Acceptance criterion | Where |
|---|---|
| Contract-compliant assessment API (201 + `bill_reference`, `payment_qr_payload`, `tigerbeetle_transfer_pending_id`) | `internal/revenue/http.go`, `service.go`; `TestCreateAssessmentHappyPath` replays the contract example |
| STIN taxpayer registry | `internal/revenue/stin.go`, `store.go` (`UpsertTaxpayer`) |
| Idempotent bill issuance | `Idempotency-Key` handling in `service.go`; `TestIdempotentBillIssuance` |
| Declarative per-state split policy, no hard-coded rates | `internal/revenue/policy.go` + `seed/*.json` via `ledger/splits` |
| Atomic statutory split on settlement | `service.go:SettleBill` → `splits.SettleGross`; `TestSettlementExecutesStatutorySplit` asserts leg amounts, clearing remainder, and ledger balances |
| Tenant isolation | `{state_id}` enum validation + per-state store partition; cross-tenant 404 tests |
| Runs without external services in CI | in-memory store + `splits.InMemoryLedger` fake; `go test ./...` needs no Postgres/TigerBeetle |
