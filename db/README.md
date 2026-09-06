# Database Migrations

PostgreSQL 16+ / PostGIS 3.4+ schema migrations for the SOS data plane. All state-data tables carry `tenant_state_id` + **Row-Level Security** keyed on `app.current_state_tenant` — tenancy isolation is enforced at the database layer (acceptance REQ-SEC-01: zero cross-tenant leakage verified by automated pen-tests).

| Migration | Scope |
|---|---|
| [0001_cadastre.sql](migrations/0001_cadastre.sql) | Cadastral parcels (PostGIS geometry, GiST indexes, RLS) — WP-06 |
| [0002_revenue_core.sql](migrations/0002_revenue_core.sql) | STIN taxpayers, assessments, bills — WP-05 |
| [0003_titling_and_luc.sql](migrations/0003_titling_and_luc.sql) | e-C-of-O titling workflow projection, signed digital titles, LUC valuation runs & bills (RLS) — WP-06 |
| [0004_environment_and_citizen.sql](migrations/0004_environment_and_citizen.sql) | ENV-09 telemetry/limits/incidents/permits/deforestation alerts/carbon registry/EIA + CIT-11 wallets/service requests/petitions/civil servants/payroll audits (RLS) — v3.0 |
| [0005_kyc_kyb.sql](migrations/0005_kyc_kyb.sql) | KYC/KYB cases, document artifacts (URI + SHA-256 only), extraction results, liveness challenges/evidence/results, beneficial owners, registry verifications, review tasks, hash-chained audit entries (RLS) — v3.0 |

## Conventions

- Migrations are forward-only, applied per-tenant-schema by the Tenant Schema Migrator (EP-CP-03).
- Monetary columns are `BIGINT` kobo — balances never mutated here; the TigerBeetle ledger is the balance source of truth.
- Spatial columns use `GEOMETRY(..., 4326)` with GiST indexes; cadastral precision validated against UTM Minna Datum (EPSG:26391/26392/26393) at ingest.
