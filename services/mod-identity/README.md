# mod-identity — Resident Registry & Governed Verification APIs

**SOS module (f) Identity & Data** (`docs/ppp-pipeline/module-state-fit.md`).
Flagship deployment: **Lagos L2 — LASRRA identity-data monetization** over
6.8M resident records [LIVE], following the **QoreID verification-API
precedent** [LIVE] (`docs/states/lagos.md`). Taraba follows on the TESIS base
[LIVE]. The NDPA-compliant consent layer is a licence condition, not an
optional feature.

## What it does

- **Resident registry** — NIN-linked, state-scoped resident records and issued
  credentials; strict per-tenant isolation across the six state tenants.
- **Consent grants (NDPA 2023)** — purpose-scoped, expiring, revocable consent
  per (resident, consumer, product). Revocation blocks all future calls.
- **Verification API products** — `ADDRESS_VERIFICATION`,
  `RESIDENCY_ATTESTATION`, `KYC_ADJUNCT`; metered per call (integer kobo),
  with revenue-share settlement records wired to the TigerBeetle chart of
  accounts (`ledger/chart-of-accounts.md`): state share → **3001 State
  Consolidated Revenue Fund** (transfer code 101), platform share → **2099 PPP
  Tech Concessionaire Escrow** (transfer code 103). In production each
  `SettlementLine` becomes one leg of a TigerBeetle two-phase transfer
  (ADR-002; SQL balance updates are prohibited).
- **Append-only audit** — every access, granted or denied, lands in a
  hash-chained audit log; `GET /audit/verify` recomputes the chain.

## NDPA posture (enforced in code)

- **Data minimization** — `VerificationResult` is structurally incapable of
  returning personal data: boolean attestation + reference IDs only. The test
  suite asserts NIN/name/address never appear in a verification response.
- **Consent before verification** — no active grant ⇒ `ConsentError` (HTTP
  403), and the denied attempt is audit-logged.
- **Revocation** — `POST /consents/{id}/revoke` blocks subsequent calls.
- **Tenant isolation** — cross-tenant access raises `TenantIsolationError`
  (HTTP 403).

## Consent-scoped authorization — Permify ReBAC mapping

Production authorization is Permify ReBAC; the consent model maps 1:1:

```
entity resident {}
entity verification_product {}
entity api_consumer {
  relation state_tenant @state
  permission verify(product, resident) =
    resident.consent_grant(product, consumer) &      // active ConsentGrant
    state_tenant.match(consumer.state_tenant)        // tenant isolation
}
```

`ConsentGrant` rows are the relationship tuples
(`resident#grants@api_consumer#purpose`); expiry/revocation delete or
tombstone the tuple, so Permify `check` denies automatically. Audit entries
mirror every permit/deny decision.

## Layout

- `app/models.py` — Resident, Credential, ApiConsumer, ConsentGrant,
  VerificationResult, UsageRecord, SettlementRecord/Line, AuditEntry.
- `app/repo.py` — repository Protocol + in-memory impl (audit store has **no
  update/delete** — append-only by construction; Postgres + RLS in production,
  every table carries `tenant_state_id`).
- `app/service.py` — tenant guard, consent lifecycle, metered verification,
  70/30 settlement split [DERIVED], hash-chain audit verification.
- `app/main.py` — FastAPI HTTP surface.

## Run / test

```bash
cd services/mod-identity
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q          # 13 tests
uvicorn app.main:app --port 8006
```

## Production notes

- **Keycloak federation** — one OIDC realm per state tenant; API consumers
  authenticate with client-credentials; `realm` claim pins `state_id`.
- **APISIX metered gateway** — consumers reach verification endpoints through
  APISIX with a metering plugin posting to this module's usage API; keys map to
  `ApiConsumer` records.
- **LASRRA ingestion** — 6.8M-record migration is a state-configured batch
  pipeline [LIVE asset]; per-call prices in `app/service.py` are [DERIVED]
  placeholders superseded by `config/states/lagos/` policy packs.
