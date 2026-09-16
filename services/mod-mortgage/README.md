# mod-mortgage — Mortgage & Lien Management

Mortgage origination and lien management for state land registries across
the tenant states (Lagos, Ogun, Osun, Benue, Nasarawa, Taraba): application
→ credit scoring → lien registration on a title → disbursement through the
platform ledger → repayment schedule → discharge (or default → foreclosure).

- **Lifecycle** — `APPLICATION → KYC_CREDIT_REVIEW → APPROVED | DECLINED`
  (with a `MANUAL_REVIEW` substate for borderline scores, approved by an
  officer with a recorded reason) `→ LIEN_REGISTERED → DISBURSED → ACTIVE →
  DISCHARGED`, or `DEFAULTED → FORECLOSED` after >90 days past due
  (simulated clock injected for tests). Foreclosure then continues through
  a full pipeline: `FORECLOSED → POSSESSION_REGISTERED` (court/consent
  reference required) `→ SALE_AUTHORIZED` (independent `valuation_kobo` +
  `reserve_price_kobo` + `valuer_id`) `→ SOLD` (gross proceeds via ledger
  hold→post from the purchaser into a sale escrow; title transferred to the
  purchaser through the fail-closed `LandTitleTransferAdapter` — if the
  title transfer fails the sale is NOT recorded) `→ PROCEEDS_DISTRIBUTED →
  CLOSED` (all parcel liens released atomically).
- **Credit scoring** — adapter seam `CreditScoringAdapter`: deterministic
  `FixtureCreditScorer` (score 300–850 derived from SHA-256 of the
  applicant id) by default; `HttpCreditScorer` behind
  `SOS_MORTGAGE_CREDIT_ENGINE=http` + `SOS_MORTGAGE_CREDIT_URL`
  (default `http://mod-ml-inference:8021/ml/v1/credit/score`), fail-closed
  (`AdapterUnavailableError`) without configuration. Score ≥ 600 approves,
  550–599 routes to manual review, < 550 declines.
- **Liens** — one active lien per parcel, unless `second_charge=true` with
  an explicit `senior_lien_id` (priority ordering, max 2 liens). Lien
  registration is gated on a **fail-closed title verification**:
  `LandRegistryAdapter.title_snapshot(state, parcel_id)` returns
  `{status, current_title_ref, owner_id, has_blocking_encumbrance}` and the
  lien requires an exact `title_ref` match, applicant == registered owner,
  status `current`, and no blocking encumbrance; the verified snapshot hash
  is recorded on the lien (`title_hash`). Fields the registry cannot attest
  (e.g. an older mod-gis-lands without lifecycle-aware verification or
  encumbrance data) make the snapshot UNVERIFIED — strict (production/HTTP)
  adapters fail closed on UNVERIFIED; the deterministic fixture
  (`strict=False`, seed via `FixtureLandRegistry.register(...)`) is lenient
  for local dev/tests. HTTP seam behind `SOS_MORTGAGE_LANDS_URL`, default
  `http://mod-gis-lands:8003`.
- **Ledger** — `MortgageLedgerAdapter` two-phase seam (PENDING hold → POST,
  VOID on failure) with deterministic 128-bit transfer ids and deterministic
  idempotency keys, integer kobo only, conservation of value (disbursed ==
  approved principal). `FixtureLedgerAdapter` replicates TigerBeetle
  semantics in-memory; `TigerBeetleLedgerAdapter` behind
  `SOS_MORTGAGE_LEDGER=tigerbeetle` + `SOS_MORTGAGE_TB_URL`, fail-closed.
- **Repayment** — equal-installment (annuity) schedule computed in exact
  integer kobo (`fractions.Fraction`, no floats); the rounding remainder
  lands on the final installment so `sum(installments) == principal +
  interest` exactly. Payments allocate oldest-first (interest then
  principal); overpayment reduces principal. A payment above the total
  outstanding is rejected with 400 by default — excess is never silently
  discarded; `allow_credit=true` accepts it and routes the excess to the
  borrower credit account (`overpayment_kobo` on the Payment +
  `credit_balance_kobo` on the mortgage), refunded with a dedicated
  treasury → borrower-credit ledger leg at discharge time. Full repayment
  discharges the mortgage and releases the lien atomically — a failed
  ledger post means no discharge. Payments are idempotent: same
  `idempotency_key` replays the same result, a conflicting amount returns
  409.
- **Foreclosure waterfall** — at `PROCEEDS_DISTRIBUTED` the sale escrow is
  distributed in exact integer kobo with deterministic transfer ids
  (`{mortgage_id}|foreclosure-distribution` + leg): **(1) sale costs,
  (2) senior lien outstanding, (3) junior lien outstanding, (4) borrower
  surplus**. All legs are two-phase (hold all → post all → void saga on
  failure). If proceeds are insufficient the shortfall is recorded as
  `deficiency_kobo` on the borrower; `sum(legs) == gross_proceeds_kobo`
  always (conservation).
- **Transactional outbox** — the API layer never publishes directly.
  Domain transitions commit `ng.sos.mortgage.*` event rows atomically with
  the state change inside `MortgageStore` (`mortgage_app/outbox.py`,
  mirroring `ledger/fundsflow/outbox.py`); a relay (`OutboxRelay`,
  best-effort drain after each request plus the explicit
  `POST /internal/outbox/relay` endpoint for tests/ops) publishes every
  un-acked row and acks only on success, so a publish failure after money
  moves is healed on the next relay pass (at-least-once; consumers dedupe
  on `idempotency_key`/`event_id`; the domain effect is exactly-once via
  deterministic ids). Seams: `InMemoryOutbox` default, `PostgresOutbox`
  behind `SOS_MORTGAGE_OUTBOX=postgres` + `SOS_MORTGAGE_OUTBOX_DSN`,
  fail-closed.
- **Audit** — every transition and every payment is appended to a
  hash-chained audit log (`services/_shared/hashchain`, tamper-evident).
- **Events** — `ng.sos.mortgage.application_received`, `credit_scored`,
  `approved`, `declined`, `lien_registered`, `disbursed`,
  `payment_applied`, `discharged`, `defaulted`, `foreclosed`,
  `possession_registered`, `sale_authorized`, `sold`,
  `proceeds_distributed`, `closed` — all delivered through the
  transactional outbox relay onto the shared `services/_shared/eventbus`
  idiom (in-memory default).
- **Stack:** Python 3.11+ · FastAPI · pydantic v2
- **Multi-tenancy:** every request is scoped by the `X-State-Tenant` header
  (missing → 400; mismatch with the path `state_id` → 404).

## Fail-closed production profile

`SOS_MORTGAGE_PROFILE=production` hard-fails at boot unless the real
adapter seams are configured — fixture adapters are never permitted in
production, mirroring the mod-ml-inference / mod-safecity-vision idiom.

| Env var | Purpose |
|---|---|
| `SOS_MORTGAGE_PROFILE` | `production` enables the fail-closed boot guard |
| `SOS_MORTGAGE_CREDIT_ENGINE` / `SOS_MORTGAGE_CREDIT_URL` | credit scorer seam (`fixture`\|`http`) |
| `SOS_MORTGAGE_LEDGER` / `SOS_MORTGAGE_TB_URL` | ledger seam (`fixture`\|`tigerbeetle`) |
| `SOS_MORTGAGE_LANDS` / `SOS_MORTGAGE_LANDS_URL` | land registry + title-transfer seam (`fixture`\|`http`) |
| `SOS_MORTGAGE_OUTBOX` / `SOS_MORTGAGE_OUTBOX_DSN` | outbox seam (`fixture`\|`postgres`); DSN required in production |

## Reference implementation (Python/FastAPI)

```bash
pip install -r services/mod-mortgage/requirements.txt
uvicorn mortgage_app.main:app --app-dir services/mod-mortgage --port 8022
cd services/mod-mortgage && python3 -m pytest tests/ -q
```

`/healthz` is always available; `/metrics` (zero-dependency Prometheus text
exposition) is wired via `services/_shared/observability.py` when the
shared package is importable, and adds mortgage counters
(`mortgage_applications_total`, `mortgage_approvals_total`,
`mortgage_disbursements_total`, `mortgage_kobo_disbursed_total`,
`mortgage_payments_total`).

## Endpoints

All under `/api/v1/states/{state_id}/mortgages`:

| Method & path | Purpose |
|---|---|
| `POST /` | Submit an application |
| `GET /` | List mortgages (`status_filter`, `applicant_id`, `parcel_id`) |
| `GET /liens?parcel_id=` | List liens (priority-ordered) |
| `GET /{id}` | Detail: status, paid-to-date, audit feed, chain validity |
| `GET /{id}/schedule` | Annuity repayment schedule + totals |
| `POST /{id}/credit-review` | Score via the credit adapter → approve/review/decline |
| `POST /{id}/approve` | Manual-review approval (`officer`, `reason`) |
| `POST /{id}/register-lien` | Register (second-charge aware) lien on the title |
| `POST /{id}/disburse` | Two-phase ledger disbursement |
| `POST /{id}/payments` | Idempotent repayment (`amount_kobo`, `idempotency_key`, `allow_credit`) |
| `POST /{id}/foreclose` | Foreclose a defaulted mortgage (`reason`) |
| `POST /{id}/possession` | FORECLOSED → POSSESSION_REGISTERED (`reference`) |
| `POST /{id}/authorize-sale` | POSSESSION_REGISTERED → SALE_AUTHORIZED (`valuation_kobo`, `reserve_price_kobo`, `valuer_id`) |
| `POST /{id}/sell` | SALE_AUTHORIZED → SOLD (`purchaser_id`, `gross_proceeds_kobo`, `sale_costs_kobo`); fails below reserve or if the title transfer fails |
| `POST /{id}/distribute-proceeds` | SOLD → PROCEEDS_DISTRIBUTED → CLOSED (waterfall) |
| `POST /internal/outbox/relay` | Relay pass: publish + ack all un-acked outbox rows |
