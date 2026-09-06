# mod-citizen-portal — Unified Citizen Portal, Sovereign Identity SSO & Civil Service Clean-Up

**SOS ticket CIT-11.** One citizen-facing portal across all six state
tenants (lagos, ogun, osun, benue, nasarawa, taraba): a single sovereign
identity wallet, Keycloak OIDC SSO, multi-MDA self-service, e-petitions,
and the civil-service biometric clean-up pipeline. This module **complements,
not replaces**, `mod-identity`, which remains the governed verification API
for registered API consumers.

## CIT-11 targets

| KPI | Target | How this module supports it |
| --- | --- | --- |
| Portal coverage | **>80%** of state services | Per-state service catalog seeded with revenue, lands, health, education and market entries, extensible via state config packs |
| Ghost-worker savings | **₦500m+** recoverable | Deterministic `PayrollAudit` rules (unverified biometric, duplicate biometric hash, duplicate salary-account hash, inactive/retired still paid) with recoverable savings computed in kobo |
| Citizen satisfaction | **CSAT >90%** | Single wallet + SSO session for every MDA service; expedited-priority lane; public petition reference IDs |

## Technology interfaces

- **Keycloak OIDC multi-realm** — one realm per state tenant
  (`sos-<state_id>`); `IdentityWallet` carries the realm/client binding and
  `POST /citizen/v1/sso/sessions` issues the OIDC authorization/session
  descriptor (redirect URI, scopes, expiry).
- **NIMC API seam** — the raw NIN is accepted only at wallet creation and
  immediately reduced to a SHA-256 hash (`hash_nin`); no read path can
  return it (`WalletRead.masked_nin`). Production wiring verifies the NIN
  against the NIMC API before wallet activation.
- **Temporal payroll verification** — each `PayrollAudit` carries a
  `TemporalWorkflowRef` placeholder (`task_queue="payroll-cleanup"`); in
  production one durable Temporal workflow per audit drives biometric
  re-verification notices, payroll holds, and recovery postings.

## What it does

- **Identity wallet / SSO facade** — `POST /citizen/v1/wallets` (smartcard
  issuance fee settled automatically), `GET /citizen/v1/wallets/{id}`,
  `POST /citizen/v1/sso/sessions`.
- **Multi-MDA self-service** — `GET /citizen/v1/services` (state catalog),
  `POST /citizen/v1/service-requests` with dynamic form payloads,
  STANDARD/EXPEDITED priority and a SUBMITTED → IN_REVIEW → APPROVED /
  REJECTED → COMPLETED status timeline; `POST .../advance` transitions.
- **E-petitions** — `POST /citizen/v1/petitions` with public reference ID,
  `POST /citizen/v1/petitions/{id}/advance` workflow; state-isolated.
- **Civil-service clean-up** — `POST /citizen/v1/civil-servants`,
  `POST /citizen/v1/biometric-verifications` (liveness gate; hashes only,
  no raw biometrics), `POST /citizen/v1/payroll-audits` (deterministic
  ghost-worker findings + total recoverable kobo), `GET .../payroll-audits/{id}`.
- **Fees / settlement** — expedited and smartcard fees split into
  settlement lines, default **70% state / 15% MDA / 15% platform**
  [DERIVED], overridable per state config; legs map to the ledger chart of
  accounts (`ledger/chart-of-accounts.md`): state share → 3001 State
  Consolidated Revenue Fund, platform share → 2099 PPP Tech Concessionaire
  Escrow.

## Privacy & tenancy posture (enforced in code)

- Raw NINs and raw biometrics are never persisted — SHA-256 hashes and
  boolean verification outcomes only.
- Every read/advance requires an explicit `state_id`; cross-tenant access
  returns HTTP 403.

## Endpoints

```
POST /citizen/v1/wallets
GET  /citizen/v1/wallets/{wallet_id}?state_id=...
POST /citizen/v1/sso/sessions
GET  /citizen/v1/services?state_id=...
POST /citizen/v1/service-requests
GET  /citizen/v1/service-requests/{request_id}?state_id=...
POST /citizen/v1/service-requests/{request_id}/advance
POST /citizen/v1/petitions
POST /citizen/v1/petitions/{petition_id}/advance
POST /citizen/v1/civil-servants
POST /citizen/v1/biometric-verifications
POST /citizen/v1/payroll-audits
GET  /citizen/v1/payroll-audits/{audit_id}?state_id=...
GET  /healthz
```

## Development

```bash
python -m pytest -q        # from services/mod-citizen-portal
python -m compileall -q app tests
```
