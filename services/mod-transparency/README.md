# mod-transparency — Public Read-Only Transparency Views

**P2 Workstream E.** Unauthenticated, tenant-scoped, read-only public
projections over two source modules:

- **mod-police-cad** — security trust fund (`GET /cad/v1/trust-fund/{state}/audit-feed`)
- **mod-ppp-investment** — concession escrow settlement statements and the
  hash-chained procurement audit log (`GET /audit`, `GET /audit/verify`)

## What it does

- **Trust-fund feed** — `GET /transparency/v1/{state_id}/trust-fund/feed`:
  donations in, disbursements out, running balance (integer kobo), and a
  per-entry hash-chain cursor so deletions/reorders are publicly detectable.
- **Escrow statements** — `GET /transparency/v1/{state_id}/escrow/statements`:
  monthly concession revenue-share settlement projections.
- **Procurement audit** — `GET /transparency/v1/{state_id}/procurement/audit`
  and `.../audit/verify`: redacted audit digests plus recomputed hash-chain
  verification (`chain_valid`, `first_invalid_seq`, `head_hash`).
- **Liveness** — `GET /healthz`.

## Redaction by construction

The read models in `app/domain.py` have **no PII fields at all**: donor
references, actor ids, bidder/contract identities and free-text details
never cross the boundary. Identities are reduced to salted SHA-256
pseudonyms (`donor_alias_hash`, `contract_ref_hash`, `subject_ref_hash`);
amounts are integer kobo. Unknown tenant states return a generic **404** —
the API never enumerates which states exist.

## Sources (`app/sources.py`)

- `InMemoryTransparencySource` — deterministic fixture default for local
  development and tests (mirrors the source modules' public shapes).
- `HttpTransparencySource` — production seam over the upstream public audit
  surfaces. **Fail-closed** (cf. mod-kyc-kyb `app/adapters/base.py`):
  requires `POLICE_CAD_BASE_URL` and `PPP_INVESTMENT_BASE_URL`; missing
  config raises at construction, upstream failures surface as HTTP 503.
  Selected automatically by `default_source()` when both env vars are set.

## Layout

- `app/domain.py` — redacted read models + hash-chain verification helpers.
- `app/sources.py` — `TransparencySource` protocol, in-memory fixture,
  fail-closed HTTP seam.
- `app/main.py` — FastAPI surface (read-only, unauthenticated).
- `tests/` — redaction, tenant isolation/non-enumeration, hash-chain tamper
  detection, fixture determinism, fail-closed seam.

## Run / test

```bash
cd services/mod-transparency
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q          # 17 tests
uvicorn app.main:app --port 8015
```

## Production notes

- The contract (`contracts/openapi/mod-transparency.yaml`) is generated from
  this app via `contracts/openapi/generate_from_apps.py` (contract-as-code).
- Endpoints are public: the generated contract marks them `security: []`
  (no bearerAuth); liveness probes are likewise unauthenticated.
- Tenant scoping is defence-in-depth on top of the API gateway's per-state
  sovereign data plane (`https://api.{state}.gov.ng/sos`).
