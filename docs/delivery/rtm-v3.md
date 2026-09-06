# SOS v3.0 Requirement Traceability Matrix (RTM)

Requirement-to-repo traceability for the v3.0 pack's exact 12 domain requirement codes **REV-01 … PPP-12**, plus the geospatial/lakehouse/financial-core platform requirements. Status values: `IMPLEMENTED` (code + tests in repo), `PARTIAL` (reference implementation present, production adapters pending), `ADAPTER-SEAM` (interface seam only; external system required).

Provenance: module and path mappings are **[LIVE]** against this repository; v3.0 business targets (SLA clocks, savings/coverage figures) are **[DERIVED]** per `docs/business/sos-v3-business-specification.md`.

## Domain requirements (v3.0 codes REV-01…PPP-12)

| Req | v3.0 domain | Spec requirement | Implementing path(s) | Tests / contracts | Status |
|---|---|---|---|---|---|
| REV-01 | Revenue | STIN master, dynamic tax calculation, assessment, billing, offline POS sync | `services/mod-rev-core/`, `db/migrations/0002_revenue_core.sql` | module tests; `contracts/openapi/` | IMPLEMENTED |
| LND-02 | Land / cadastre / LUC | Cadastral parcels, e-C-of-O titling workflow, satellite-footprint LUC valuation | `services/mod-gis-lands/`, `services/mod-gis-luc/`, `db/migrations/0001_cadastre.sql`, `0003_titling_and_luc.sql`, `geospatial/sedona/unassessed_property_join.sql` | module tests; `geospatial/tests/`; event `ng.sos.gis.unassessed_property_discovered`; RLS policies | IMPLEMENTED |
| EXT-03 | Extractives / mining | Mineral custody, weighbridge IoT, levies; timber provenance & forestry NDVI alerts | `services/mod-mining/`, `services/mod-forestry/`, `geospatial/sedona/ndvi_change_detection.py` | module tests; `contracts/asyncapi/mining-events.yaml`, `forestry-events.yaml` | IMPLEMENTED |
| AGR-04 | Agriculture | Produce e-waybills, warehouse receipts, agro-hub traceability | `services/mod-agri-waybill/` | module tests | IMPLEMENTED |
| TRN-05 | Transport / transit / WIM | Weigh-in-motion overload fines, ANPR, multimodal transit clearing (Cowry-compatible) | `services/mod-transport-wim/`, `services/mod-mobility-switch/` | module tests | IMPLEMENTED |
| MKT-06 | Markets | Market stall cadastre, micro-tenancy billing | `services/mod-market/` | module tests | IMPLEMENTED |
| HLT-07 | Health | Hospital billing, SHIA/NHIS claims | `services/mod-health/` | module tests | IMPLEMENTED |
| EDU-08 | Education | Tertiary consolidated billing & bursary | `services/mod-education/` | module tests | IMPLEMENTED |
| ENV-09 | Environment / carbon / emissions | Industrial telemetry compliance, effluent/timber permits, deforestation surveillance (<4h SLA), carbon registry (issue/transfer/retire), EIA workflow | `services/mod-environment/`, `db/migrations/0004_environment_and_citizen.sql`, `config/states/*/modules.yaml` | `services/mod-environment/tests/`; channels `ng.sos.environment.*` in `contracts/asyncapi/platform-events.yaml` | IMPLEMENTED (reference impl.; IoT/satellite ingestion is ADAPTER-SEAM) |
| SEC-10 | Safe City / security | 112 CAD dispatch, patrol geofencing; consumes ENV-09 enforcement ticket references | `services/mod-police-cad/` | module tests | IMPLEMENTED (ratification-gated rollout) |
| CIT-11 | Citizen Portal / Identity / Civil Service | Identity wallet & Keycloak SSO, service catalog/requests, e-petitions, biometric payroll audit (ghost-worker rules) | `services/mod-citizen-portal/`, `db/migrations/0004_environment_and_citizen.sql` | `services/mod-citizen-portal/tests/`; channel `ng.sos.citizen.payroll_audit_completed` | IMPLEMENTED (reference impl.; NIMC/Keycloak realms are ADAPTER-SEAM) |
| PPP-12 | Concession / revenue waterfall | PPP pipeline, escrowed statutory splits, concession ceiling guardrails | `services/mod-ppp-investment/`, `config/states/*/policy-pack.json` | `config/states/validate_packs.py` guardrails; `ng.sos.payments.settlement_completed` | IMPLEMENTED |

## Geospatial / lakehouse / financial-core requirements

| Req | Spec requirement | Implementing path(s) | Tests / contracts | Status |
|---|---|---|---|---|
| GEO-01 | Dual-engine spatial: PostGIS OLTP + Sedona analytics (ADR-004) | `geospatial/sedona/`, `db/migrations/0001_cadastre.sql` | `geospatial/tests/` | IMPLEMENTED |
| GEO-02 | SedonaDB (Rust/DataFusion) embedded spatial analytics + H3 hexagonal indexing for alerts | `geospatial/README.md` (v3.0 alignment); H3 cell IDs on ENV-09 deforestation alerts | `services/mod-environment/tests/` | PARTIAL (documented target; embedded engine adoption pending) |
| GEO-03 | GeoParquet / Delta Lake WKB lakehouse storage (Silver→Gold) | `services/lakehouse/`, `geospatial/` local runners accept GeoParquet | `geospatial/tests/` | IMPLEMENTED |
| GEO-04 | State GIS agency integration: NAGIS, TAGIS, LASGIS, BENGIS | `geospatial/README.md` integration notes | — | ADAPTER-SEAM |
| GEO-05 | NDVI change detection ≥0.5 ha within 72h (Sentinel-2/Landsat) | `geospatial/sedona/ndvi_change_detection.py`, `geospatial/local/ndvi_change_detection.py` | `geospatial/tests/` | IMPLEMENTED (local verification mode) |
| LKH-01 | Delta Lake medallion, Flink streaming, Ray AI lakehouse | `services/lakehouse/` | module tests | IMPLEMENTED |
| FIN-01 | TigerBeetle double-entry ledger; kobo integer money; deterministic splits (3001 CRF/TSA, 2099 concession escrow; 5001 never credited) | `ledger/chart-of-accounts.md`, `config/states/*/policy-pack.json` | `config/states/validate_packs.py`; settlement event contract | IMPLEMENTED |
| FIN-02 | Mojaloop clearing + real-time settlement webhook | settlement flow in `contracts/asyncapi/platform-events.yaml` (`ng.sos.payments.settlement_completed`) | contract schema | ADAPTER-SEAM (Mojaloop switch external) |
| FIN-03 | ENV-09 revenue legs (effluent permits, carbon brokerage, timber royalties, violation fines) in kobo | `services/mod-environment/`, `db/migrations/0004_environment_and_citizen.sql` | module tests | IMPLEMENTED (reference impl.) |
| FIN-04 | CIT-11 fee settlement default 70/15/15 (state/MDA/platform) with state override | `services/mod-citizen-portal/` | module tests | IMPLEMENTED (reference impl.) |
| SEC-01 | Zero cross-tenant leakage: `tenant_state_id` scoping + Postgres RLS on all tenant tables | `db/migrations/0001–0004`, service repositories | RLS policies; pen-test gate (REQ-SEC-01) | IMPLEMENTED |
