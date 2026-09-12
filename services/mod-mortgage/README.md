# mod-mortgage — Mortgage & Lien Management

Mortgage origination and lien management for state land registries across
the tenant states (Lagos, Ogun, Osun, Benue, Nasarawa, Taraba): application
→ credit scoring → lien registration on a title → disbursement through the
platform ledger → repayment schedule → discharge (or default → foreclosure).

- **Lifecycle** — `APPLICATION → KYC_CREDIT_REVIEW → APPROVED | DECLINED`
  (with a `MANUAL_REVIEW` substate for borderline scores, approved by an
  officer with a recorded reason) `→ LIEN_REGISTERED → DISBURSED → ACTIVE →
  DISCHARGED`, or `DEFAULTED → FORECLOSED` after >90 days past due
  (simulated clock injected for tests).
- **Credit scoring** — adapter seam `CreditScoringAdapter`: deterministic
  `FixtureCreditScorer` (score 300–850 derived from SHA-256 of the
  applicant id) by default; `HttpCreditScorer` behind
  `SOS_MORTGAGE_CREDIT_ENGINE=http` + `SOS_MORTGAGE_CREDIT_URL`
  (default `http://mod-ml-inference:8021/ml/v1/credit/score`), fail-closed
  (`AdapterUnavailableError`) without configuration. Score ≥ 600 approves,
  550–599 routes to manual review, < 550 declines.
- **Liens** — one active lien per parcel, unless `second_charge=true` with
  an explicit `senior_lien_id` (priority ordering, max 2 liens). Parcel and
  title are confirmed against the land registry (`LandRegistryAdapter`:
  fixture validates the C-of-O title-ref format; HTTP seam behind
  `SOS_MORTGAGE_LANDS_URL`, default `http://mod-gis-lands:8003`).
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
  principal); overpayment reduces principal; full repayment discharges the
  mortgage and releases the lien atomically — a failed ledger post means no
  discharge. Payments are idempotent: same `idempotency_key` replays the
  same result, a conflicting amount returns 409.
- **Audit** — every transition and every payment is appended to a
  hash-chained audit log (`services/_shared/hashchain`, tamper-evident).
- **Events** — `ng.sos.mortgage.application_received`, `credit_scored`,
  `approved`, `declined`, `lien_registered`, `disbursed`,
  `payment_applied`, `discharged`, `defaulted`, `foreclosed` published via
  the shared `services/_shared/eventbus` idiom (in-memory default).
- **Stack:** Python 3.11+ · FastAPI · pydantic v2
- **Multi-tenancy:** every request is scoped by the `X-State-Tenant` header
  (missing → 400; mismatch with the path `state_id` → 404).

## Fail-closed production profile

`SOS_MORTGAGE_PROFILE=production` hard-fails at boot unless the real
adapter seams are configured (`SOS_MORTGAGE_TB_URL` and
`SOS_MORTGAGE_LANDS_URL`) — fixture adapters are never permitted in
production, mirroring the mod-ml-inference / mod-safecity-vision idiom.

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
| `POST /{id}/payments` | Idempotent repayment (`amount_kobo`, `idempotency_key`) |
| `POST /{id}/foreclose` | Foreclose a defaulted mortgage (`reason`) |
