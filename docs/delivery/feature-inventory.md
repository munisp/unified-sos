# SOS Feature Inventory — Complete Audit-Ready Register

**Stage 4.1 deliverable** · Branch `feat/feature-inventory` · Date 2026-09-06

This document enumerates **every implemented and remaining SOS feature** discovered by a
full-repo scan of `docs/`, `services/`, `contracts/`, `db/`, `config/`, `infra/`, `edge/`,
`ledger/`, `geospatial/`, `tools/`, and `tests/`. Each feature is mapped to its user /
stakeholder, onboarding path, KYC/KYB requirement, data class, integration / adapter, legal
/ policy gate, infrastructure dependency, implementation status, and next requirement.

## Status vocabulary

| Status | Meaning |
|---|---|
| `IMPLEMENTED` | Runnable, tested reference implementation in-repo (code + tests green in CI scope). |
| `PARTIAL` | Core reference implemented; production wiring (cluster adapter, gateway binding, hardware binding) documented but not live. |
| `ADAPTER-SEAM` | Interface/seam defined in code (interface class, stub, or documented mapping); the production adapter (TigerBeetle cluster, Kafka, Temporal server, NIMC, CAC, Mojaloop scheme, hardware SE, Wazuh, etc.) is intentionally unimplemented. |
| `GAP` | Named requirement with no in-repo implementation beyond docs/architecture references. |

All amounts are integer kobo; all tenant-owned objects are scoped to
`tenant_state_id ∈ {lagos, ogun, osun, benue, nasarawa, taraba}`.

---

## 1. Master feature register

### 1.1 Platform foundation & control plane

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-001 | Control plane | Tenant provisioning API (create → provisioning → active/suspended) | `services/control-plane/app/main.py`, `app/domain.py`; contract `contracts/openapi/control-plane.yaml` | State administrators, platform operators | `sosctl tenant create --state=<s> --tier=<t>` (GitOps commit) | Operator identity via Keycloak admin realm; no citizen data | Tenant metadata only (zero citizen PII — enforced by `app/pii_guard.py`) | K8s namespaces, Postgres schemas (RLS), Keycloak realms, S3 buckets, KMS keyrings — ADAPTER-SEAM to live provisioning | NDPA 2023 residency; tenancy charter | RKE2/EKS clusters, ArgoCD, KMS | PARTIAL | Bind API to real provisioning operators (Go/Dapr CRDs per blueprint) |
| F-002 | Control plane | Policy-pack registry & guardrail validation | `services/control-plane/app/policy.py`; schema `contracts/policy-packs/revenue-split.schema.json` | State administrators, finance commissioners | `sosctl policy apply --state --file` | Operator credential only | Policy/config data | OPA Rego hooks (documented); jsonschema validation implemented | Gazette reference required per pack | OpenSearch audit archive (seam) | PARTIAL | OPA Rego runtime binding; guardrail rule library |
| F-003 | Control plane | Append-only audit-event feed | `GET /control/v1/audit-events` in `services/control-plane/app/main.py` | Auditors, administrators | Service-to-service | n/a | Audit metadata | OpenSearch immutable archive, 7-yr retention — ADAPTER-SEAM | Procurement Clause: auditability | OpenSearch cluster | PARTIAL | Hash-chain the control-plane feed; OpenSearch sink |
| F-004 | IAM | Multi-realm Keycloak (one realm per state tenant) | `deploy/keycloak/realm-sos-dev.json`; `infra/helm/sos-platform/templates/keycloak-realm-import-job.yaml`; ADR-006 | All users (citizens, civil servants, MDA staff, agents, vendors) | Realm per state; OIDC PKCE | NIN federation at realm level (WP-02) — seam | Identity/credentials | NIMC/NIN & CAC federation gateway — ADAPTER-SEAM (WP-02 EP-IAM-02) | NDPA 2023; FIDO2 for revenue approvers; MFA for admins | Keycloak HA cluster | PARTIAL | NIMC/CAC federation adapters; per-state realm templates for all 6 states |
| F-005 | Operator tooling | `sosctl` CLI (tenant/policy/ledger commands) | `tools/sosctl/` (typer + rich), tests | Platform operators | CLI + GitOps commits | Operator credential | Config/metadata | ArgoCD GitOps; control-plane bundle parity | 100% IaC acceptance (WP-16) | Git/ArgoCD | IMPLEMENTED (reference) | Wire to live ArgoCD API; signed commits |
| F-006 | DevSecOps | CI / SBOM / security-scan workflows | `.github/workflows/ci.yml`, `sbom.yml`, `security-scan.yml` | Engineering, security | n/a | n/a | n/a | GitHub Actions, Spectral/AsyncAPI lint | Clause 19.4 contracts-first | GitHub | IMPLEMENTED | Expand security-scan to blocking OWASP gates |
| F-007 | Infra | K8s base + per-state overlays (shared/hybrid/dedicated tiers) | `infra/k8s/base`, `infra/k8s/overlays/*`; validated by `infra/tests/validate_infra.py` | Platform operators | GitOps | n/a | Infra config | Cilium default-deny NetworkPolicies | Tenancy isolation matrix (arch 06) | RKE2/EKS | IMPLEMENTED (manifests) | Live cluster reconciliation; Cilium Hubble observability |
| F-008 | Infra | Helm chart (APISIX, KEDA, Kubecost, Keycloak import, mod-rev-core) | `infra/helm/sos-platform/` | Platform operators | Helm install | n/a | Infra config | APISIX + OpenAppSec WAF (ADR-007) | Zero-trust ingress pipeline | K8s | PARTIAL | All 18 modules as chart templates; OpenAppSec policy pack |
| F-009 | Infra | Terraform sovereign infra (cluster, KMS keyring, object storage) | `infra/terraform/modules/*`, `envs/tier{1,2,3}-*/` | Platform operators | Terraform apply | n/a | Infra config | In-country Tier-3 DC / sovereign cloud | NDPA residency | Sovereign cloud | PARTIAL | Remote state backend; environment pipelines |
| F-010 | Infra | ArgoCD GitOps project + per-state Applications | `infra/gitops/projects`, `infra/gitops/applications/sos-*.yaml` | Platform operators | Git commit | n/a | Infra config | ArgoCD | 100% IaC (WP-16) | ArgoCD | IMPLEMENTED (manifests) | App-of-apps wiring; drift alerting |

### 1.2 Financial core (ledger, clearing, revenue)

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-011 | Ledger | TigerBeetle chart of accounts & 128-bit account-ID taxonomy | `ledger/chart-of-accounts.md`; `ledger/splits/account_id.go` | Finance officers, auditors | `sosctl ledger init-chart --state` | n/a | Financial (authoritative balances) | TigerBeetle cluster (ADR-002) | Clause 22.2 non-negotiables; federal royalty separation by construction (5xxx) | TigerBeetle cluster | IMPLEMENTED (taxonomy + builder) | Provision clusters per tier |
| F-012 | Ledger | Deterministic statutory split engine + atomic linked-transfer chain | `ledger/splits/split.go`, `chain.go`, `policy.go`, `cmd/atomic-split`; tests | Finance officers, concessionaires | Policy pack load | n/a | Financial | `LedgerClient` interface + `InMemoryLedger`; production tigerbeetle-go adapter documented | Gazette-anchored split packs; ≤8%/≤15% concession ceilings | TigerBeetle | PARTIAL | `tigerbeetle-go` adapter behind `LedgerClient` |
| F-013 | Revenue | Core revenue & automated assessment engine (STIN, assessments, bills, idempotency, settlement webhook) | `services/mod-rev-core/` (Go); contract `contracts/openapi/revenue-assessments.yaml`; schema `db/migrations/0002_revenue_core.sql`; seeds `internal/revenue/seed/*` | Taxpayers (citizens, corporates), MDA revenue officers, POS agents | STIN issuance linked to NIN/BVN/CAC — seam | **KYC: NIN/BVN linkage for STIN; KYB: CAC for corporate taxpayers — ADAPTER-SEAM** | Financial + taxpayer PII (minimized) | NIBSS e-Bills/QR, Mojaloop clearing (WP-04) — ADAPTER-SEAM; APISIX JWT trust | Gazetted tax laws per state; 100k assessments zero-discrepancy acceptance | Postgres RLS, Redis, Temporal, TigerBeetle | PARTIAL | TigerBeetle prod adapter (`REV_CORE_LEDGER=tigerbeetle` stub); NIBSS/Mojaloop scheme adapters |
| F-014 | Payments | Interoperable clearing switch (Mojaloop FSPIOP, NIBSS e-Bill gateway, escrow) | Documented WP-04; webhook seams in mod-rev-core, mod-education (`/webhooks/mojaloop` stub), mod-mobility-switch | Banks, PSPs, agents, taxpayers | Bank/PSP scheme onboarding | KYB for participating PSPs/banks | Financial | Mojaloop FSPIOP — ADAPTER-SEAM; NIBSS — ADAPTER-SEAM | CBN/NIBSS scheme rules | Mojaloop deployment | ADAPTER-SEAM | Mojaloop connector deployment + scheme certification |
| F-015 | Revenue | Offline-first POS collection (signed tickets, ≥5,000 offline cache) | `edge/edge-daemon/` (models, Ed25519 crypto, SQLite outbox, sync engine, fake gateway); wire-compat proven with `mod-market` | POS/field agents, market traders | Agent device enrollment + key issuance | **KYC for agent onboarding (NIN + biometric) — GAP** (planned Stage 4.2/4.3) | Financial + device identity | Hardware secure element (SE) — ADAPTER-SEAM; mTLS sync hooks; Fluvio | WP-05 acceptance (5,000 signed offline txns) | Ruggedized Android POS, APISIX | PARTIAL | Rust daemon on Android + SE-backed signer; agent KYC |
| F-016 | Ledger | Trust-fund / escrow transparency (police trust fund, concession escrow 2099) | `services/mod-police-cad` trust-fund ledger refs; `mod-ppp-investment` settlement statements; `ledger/chart-of-accounts.md` | Donors, concessionaires, auditors | Account initialization | n/a | Financial | TigerBeetle | Public auditability requirement | TigerBeetle | PARTIAL | Public read-only audit views |

### 1.3 Identity, citizen services & civil service

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-017 | Identity | Resident registry (NIN-linked, tenant-scoped) + issued credentials | `services/mod-identity/app/` (`models.py`, `repo.py`, `service.py`) | Citizens, residents | NIN-linked record creation | **KYC: NIN verification vs NIMC — ADAPTER-SEAM (raw NIN → SHA-256 at creation)** | Citizen PII (minimized, hashed) | NIMC API seam; LASRRA/TESIS base data | NDPA 2023 consent layer = licence condition | Postgres RLS | PARTIAL | NIMC adapter; biometric dedupe |
| F-018 | Identity | NDPA consent grants (purpose-scoped, expiring, revocable) + hash-chained audit + `GET /audit/verify` | `services/mod-identity/app/service.py` | Citizens (grantors), verification API consumers, auditors | Consumer registration → consent request → grant | KYB for API consumers (licensed businesses) — PARTIAL (registry exists, verification seam) | Consent metadata, audit | Permify ReBAC mapping (documented 1:1) | NDPA 2023 | OpenSearch audit archive (seam) | IMPLEMENTED (reference) | Permify deployment; consumer KYB verification |
| F-019 | Identity | Governed verification API products (ADDRESS_VERIFICATION, RESIDENCY_ATTESTATION, KYC_ADJUNCT) metered per call w/ revenue-share settlement | `services/mod-identity/app/service.py` | Licensed API consumers (banks, telcos, fintechs), state IGR | Consumer KYB → product subscription → consent-scoped calls | KYB consumer onboarding; KYC_ADJUNCT product feeds third-party KYC | Minimized attestation (boolean + refs; structurally no PII) | TigerBeetle settlement legs (101/103); QoreID precedent | NDPA data-minimization (enforced in code + tests) | TigerBeetle | IMPLEMENTED (reference) | Metering/billing pipeline to lakehouse Gold |
| F-020 | Citizen services | Unified citizen portal: identity wallet, SSO sessions, per-state service catalog, multi-MDA service requests (STANDARD/EXPEDITED) | `services/mod-citizen-portal/app/` (`domain.py`, `service.py`, `repository.py`); schema `db/migrations/0004...` | Citizens, MDA service officers | Wallet creation (smartcard fee settled automatically) → SSO session | **KYC: NIN hash at wallet creation; production NIMC verification before activation — ADAPTER-SEAM** | Citizen PII (masked NIN read paths), service requests | Keycloak OIDC multi-realm; NIMC seam | NDPA; >80% portal coverage KPI | Keycloak, Postgres | PARTIAL | NIMC pre-activation check; per-MDA form definitions (Form.io seam) |
| F-021 | Citizen services | E-petitions with public reference IDs | `services/mod-citizen-portal` (petitions) | Citizens, MDA officers | Wallet → petition submission | KYC (wallet holder) | Citizen PII + petition content | — | CSAT >90% KPI | Postgres | IMPLEMENTED (reference) | MDA routing SLA dashboards |
| F-022 | Civil service | Payroll biometric clean-up (PayrollAudit: unverified biometric, duplicate biometric/salary-account hashes, inactive-still-paid; recoverable savings in kobo) | `services/mod-citizen-portal/app/domain.py` + service | Civil servants, HR/payroll officers, auditors | Civil-servant record import → audit run | **KYC: biometric re-verification of civil servants — ADAPTER-SEAM (Temporal workflow ref placeholder)** | Employee PII + payroll | Temporal `payroll-cleanup` queue — ADAPTER-SEAM; biometric capture devices — GAP | ₦500m+ ghost-worker savings KPI | Temporal | PARTIAL | Biometric capture/enrollment pipeline; durable Temporal workflow |
| F-023 | Identity | **KYC/KYB capability (document OCR, Docling understanding, VLM adjudication seam, liveness, KYB registry verification, risk scoring, review queues)** | _Not yet implemented — Stage 4.2/4.3 scope (`services/mod-kyc-kyb`, `packages/document-ai/`)_ | All stakeholder classes (citizens, agents, vendors, corporates) | Per stakeholder onboarding doc (`docs/governance/stakeholder-onboarding.md`) | This **is** the KYC/KYB capability | Identity documents (stored as object refs/hashes only), biometrics (refs only), verification results (minimized) | PaddleOCR, Docling, VLM, liveness, NIMC, CAC registry — all ADAPTER-SEAM by design | NDPA 2023; sovereignty (no inline PII); hash-audited, tenant-scoped | Object storage, GPU (VLM), Keycloak | **GAP** | Implement per SPEC-KYC-KYB (Stage 4.2/4.3) |

### 1.4 Land, geospatial & valuation

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-024 | Cadastre | Parcel registry w/ PostGIS geometry (EPSG:4326 storage, UTM Minna 26391–93 validation), topological overlap checks | `services/mod-gis-lands/lands_app/geometry.py`, `repository.py`; schema `db/migrations/0001_cadastre.sql`; contract `contracts/openapi/cadastre-parcels.yaml` | Surveyors, landowners, lands MDA staff | Parcel registration (surveyor-submitted) | KYC of applicant (surveyor licence + landowner NIN) — seam | Land cadastre + owner PII | NAGIS/BENGIS/TAGIS/LASGIS/OLARMS/OGIS migration — ADAPTER-SEAM | State land laws; zero-overlap acceptance | PostGIS | PARTIAL | `PostGISParcelRepository` (stub documented); legacy GIS migration ETL |
| F-025 | Cadastre | e-C-of-O titling workflow (Surveyor → Town Planning → AG → Governor digital signature) + SLA clocks per state | `lands_app/titling.py`, `sla.py`, `temporal_adapter.py`; migration `0003_titling_and_luc.sql` | Landowners, surveyors, town planning, AG, Governor's office | Application intake → multi-stage approval | KYC of applicant; signatory credentials per approver role | Land title + PII | Temporal server — ADAPTER-SEAM (`LocalTitlingRunner` for tests) | C-of-O < 14 days acceptance; Osun 45 d / Benue 60–90 d SLAs | Temporal | PARTIAL | Temporal deployment + per-state task queues |
| F-026 | Cadastre | Cryptographically signed digital titles (Ed25519 compact JWS, registry + governor-consent chain) | `lands_app/signing.py` | Landowners, banks (collateral verification), auditors | Title issuance → `verifyDeed` | n/a (title verification is public) | Signed title artifacts | HSM/KMS key custody — ADAPTER-SEAM | Non-repudiation requirement | KMS/HSM | IMPLEMENTED (reference) | KMS-backed signing keys; revocation lists |
| F-027 | LUC | Land Use Charge valuation (Sedona footprint↔parcel join, tariff packs, reliefs, bills) | `services/mod-gis-luc/luc_app/` (calculator, tariffs, ingestion, repository); spatial job `geospatial/sedona/unassessed_property_join.sql`; event `ng.sos.gis.unassessed_property_discovered` | Property owners, revenue officers | Footprint discovery → assessment → bill | KYC of assessed owner (NIN/STIN link) — seam | Property + owner PII | Sedona/DataFusion — ADAPTER-SEAM; Ray AVM feed — seam | Per-state LUC laws; policy-pack tariffs | Sedona, DataFusion | PARTIAL | Sedona cluster run of join; AVM model integration |
| F-028 | Geospatial | NDVI deforestation change detection (Sedona + local pandas impls, synthetic fixtures) | `geospatial/sedona/ndvi_change_detection.py`, `geospatial/local/ndvi_change_detection.py`, fixtures, tests | Environment/forestry officers | Job ingestion → alerts | n/a | Satellite raster (Sentinel-2/Landsat) | Sedona cluster — ADAPTER-SEAM; consumers mod-forestry/mod-environment | >0.5 ha flagged < 72 h | Sedona, Delta Lake | PARTIAL | Scheduled production runs + alert bus publishing |
| F-029 | Geospatial | Unassessed-property spatial join (footprints vs parcels) | `geospatial/sedona/unassessed_property_join.sql`, `local/unassessed_property_join.py`, tests | Revenue officers | Job → LUC ingestion | n/a | Property geodata | DataFusion/Sedona | 1M-join < 5 s acceptance | Sedona | PARTIAL | Cluster-scale benchmark |

### 1.5 Natural resources & environment

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-030 | Mining | Mineral e-permit registry, consignments (CREATED→WEIGHED→DISPATCHED→DELIVERED), weighbridge readings, XRF assay, levy assessment; state vs federal royalty separation by construction | `services/mod-mining/app/` (models, levy, service, repo, bus); contract `contracts/asyncapi/mining-events.yaml` | Miners, quarry operators, mineral-agency officers, checkpoint agents | Site registration → permit → consignment | **KYB for mining companies (CAC + licence); KYC for artisanal miners (biometric registry named, not built) — GAP for biometric part** | Corporate + operational; miner PII (biometric registry pending) | Kafka bus — ADAPTER-SEAM (`InMemoryEventBus` + documented `KafkaEventBus`); ThingsBoard IoT — seam; RFID manifests — seam | State-competent levies only (HARD CONSTRAINT); NEITI/Federal MoU | Kafka, PostGIS | PARTIAL | Kafka adapter build; artisanal biometric registry (depends on F-023) |
| F-031 | Transport | Weigh-in-motion overload detection, ANPR↔WIM plate correlation (±30 s), automated fines (code 120), e-manifest checkpoint verify | `services/mod-transport-wim/app/`; shared envelope `ng.sos.mining.weighbridge_reading` | Transporters/haulage drivers, corridor operators, checkpoint agents | Corridor config (gazetted limits) → sensor ingest | KYB for haulage firms; vehicle/manifest identity | Vehicle/plate data (quasi-PII) | Fluvio edge streaming — ADAPTER-SEAM; ANPR cameras — seam; Flutter field POS — seam | Gazette corridor limits; fine < 3 s acceptance | Fluvio, TigerBeetle, APISIX | PARTIAL | Fluvio pipeline + ANPR adapter; roadside hardware integration |
| F-032 | Forestry | UHF RFID nail-tag provenance (ISSUED→HARVESTED→IN_TRANSIT→MILLED; SEIZED), licensed-coupe enforcement, transit permits, stumpage billing, untagged-haulage alerts | `services/mod-forestry/app/`; events `contracts/asyncapi/forestry-events.yaml` | Loggers, rangers, forestry officers | Coupe licence → tag issuance → harvest | KYB for timber licensees; ranger credentialing | Operational | Ranger GPS scanners — seam; OpenCTI — seam; NDVI job (F-028) | Rosewood cartel suppression KPIs | PostGIS, Delta Lake | PARTIAL | RFID hardware + scanner app; bus publishing to Kafka |
| F-033 | Environment | Industrial IoT telemetry compliance, effluent/timber permits (DRAFT→ACTIVE→SUSPENDED/EXPIRED), violation fines w/ per-state multipliers | `services/mod-environment/app/` | Facilities (industrial), environment officers | Facility registration → permit | **KYB for facilities (CAC + EIA status) — seam** | Facility corporate + telemetry | IoT ingestion edge — seam; event bus seam (`ng.sos.environment.*`) | LASEPA/state environmental laws; < 4 h alert SLA | IoT platform, Kafka | PARTIAL | IoT platform adapter (ThingsBoard); bus publishing |
| F-034 | Environment | Carbon registry (projects, credits, issuance, transfer w/ 3% brokerage, retirement) | `services/mod-environment/app/domain.py` + main | Carbon project developers, buyers, state | Project registration → credit issuance | KYB for project developers/buyers | Corporate + market data | TigerBeetle settlement legs (account 3001) | Carbon-market rules `[DERIVED]` defaults | TigerBeetle | IMPLEMENTED (reference) | External registry (Verra/GS) cross-listing seam |
| F-035 | Environment | EIA workflow, deforestation alert dispatch (SEC-10 ticket refs, 4 h SLA) | `services/mod-environment/app/main.py` | Facilities, EIA consultants, enforcement | EIA application submission | KYB for applicants | Corporate + environmental | Sedona NDVI ingestion (F-028); mod-police-cad dispatch seam | EIA Act; < 4 h response KPI | Sedona | PARTIAL | Alert pipeline automation; CAD ticket integration |

### 1.6 Agribusiness, markets & mobility

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-036 | Agri | E-waybills w/ HMAC-signed QR (`SOSWB1.*`), border verification (< 10 s), checkpoint tracking, warehouse receipts (delivered-consistency gated) | `services/mod-agri-waybill/app/` | Farmers, transporters, warehouse operators, border agents | Consignment creation → QR issuance | KYB for agro-hubs/warehouses; KYC for farmers (informal) | Consignment + actor refs | HMAC key from secret store — ADAPTER-SEAM; TigerBeetle (code 140); EUDR provenance | EUDR deforestation-free export (cocoa/tea) | Redis, PostGIS | PARTIAL | Secret-store wiring; EUDR traceability passport export |
| F-037 | Markets | Market/stall/trader registry, daily stallage tickets (code 130, double-charge prevention), dispute workflow (append-only arbitration trail), signed offline batch ingestion w/ Ed25519 verify + (device,seq) dedupe | `services/mod-market/app/`; wire-compat test w/ real edge daemon (`tests/test_edge_ingestion.py`) | Market traders, POS agents, market authorities, concessionaires | Stall titling/lease → trader assignment → daily collection | **KYC for traders (NIN, informal-sector tiered KYC); KYC+device binding for agents — GAP (Stage 4.3)** | Trader PII + financial | Edge daemon (implemented); TigerBeetle escrow; USSD/POS collection — seam; Form.io — seam | > 95% collection acceptance; middleman-leakage elimination | TigerBeetle, PostGIS | PARTIAL | USSD gateway adapter; concession lease contract UI (Form.io) |
| F-038 | Mobility | Multimodal transit clearing: fare tables (union commission 3–8% band), tap clearing, operator settlement batches (codes 140; accounts 4002/3001/2010; legs sum to gross), Cowry-compatible card bridge stub | `services/mod-mobility-switch/app/` | Commuters, transport operators, unions, drivers | Operator onboarding → fare table → settlement | KYB for operators/unions; driver manifests (KYC) — seam | Financial + operational | Cowry Gen 2 bridge — ADAPTER-SEAM (offline interface contract); Mojaloop QR — seam | LAMATA ticketing harmonization gazette | Redis, TigerBeetle | PARTIAL | Cowry production bridge; Mojaloop QR scheme |
| F-039 | Mobility/edge | Ruggedized POS & checkpoint hardware profiles (solar kiosks, WIM controllers, ANPR corridors) | `edge/README.md` profiles; daemon reference F-015 | Field agents | Device enrollment | Device identity + agent KYC | Device telemetry | Hardware SE — ADAPTER-SEAM; mTLS | WP-08 M6.x milestones | Hardware supply chain | ADAPTER-SEAM | Procurement of hardware; SE signer binding |

### 1.7 Health, education, safety, PPP

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-040 | Health | Facility billing accounts, invoices/payments, SHIA/NHIS claim lifecycle (submitted→adjudicated→paid/rejected), pharmacy stock-aware drug billing (409 stock_out) | `services/mod-health/app/` | Patients, hospital staff (facilities), SHIA/NHIS | Facility onboarding → billing accounts | KYC for patient billing accounts (NIN optional); KYB for facilities | **Health PII (sensitive)** | FHIR compliance (named); SHIA/NHIS claims API — ADAPTER-SEAM | NDPA sensitive-data class; 100% e-collection acceptance | Postgres RLS, Temporal | PARTIAL | FHIR resource mapping; NHIS adapter; facility KYB |
| F-041 | Education | Consolidated student billing (invoices, payment), course-registration payment lock (HTTP 423), Mojaloop webhook stub | `services/mod-education/app/` | Students, bursary staff, institutions | Institution onboarding → student billing | KYC for students (matric + NIN); KYB for institutions | Student PII + financial | Mojaloop clearing — ADAPTER-SEAM; institution portals (UNIOSUN etc.) — seam | University-treasury ↔ CRF reconciliation | Postgres, Mojaloop | PARTIAL | Institution SIS integrations; Mojaloop adapter |
| F-042 | Public safety | Multi-agency CAD: incidents, 112 dispatch, geofenced patrols, CCTV/drone metadata routing, trust-fund ledger | `services/mod-police-cad/app/` (domain, gate, main) | Police/vigilante dispatchers, officers, community vigilantes (Amotekun, So-Safe, BSCPG, LNSC) | Agency onboarding → unit enrollment | KYC for enrolled officers/vigilantes; vetting — seam | Sensitive law-enforcement | Kafka, Wazuh, WebRTC — ADAPTER-SEAMs | NPSCS design rules (O&M, due-process, asset registry) | Kafka, PostGIS | PARTIAL | Dispatch UI; Wazuh binding; WebRTC gateway |
| F-043 | Public safety | **Constitutional ratification gate encoded in code** (`RatificationGate.ratified` defaults FALSE; arms register & state-force stand-up → HTTP 403 until ≥24/36 assemblies + assent) | `services/mod-police-cad/app/gate.py` | State governments, auditors | Certified tally input only (never tenant config) | n/a | Governance | — | **Constitutional amendment + Ebubeagu precedent — hard legal gate** | n/a | IMPLEMENTED | Certified tally ingestion process |
| F-044 | PPP | Pipeline registry (PIPELINE→…→CLOSED), public disclosure view (structural redaction), unsolicited proposals w/ DOC-01…08 checklist, QCBS 1,000-pt scoring (560 threshold; commercial gating), OBC/FBC doc sets | `services/mod-ppp-investment/app/` (models, repo, service) | Vendors/concessionaires, PPP offices (TARIPA, NASIDA, Ogun PPP Office, BIPC/BDIC), evaluators | Vendor registration → proposal submission → screening | **KYB for vendors: CAMA 2020, FIRS TCC, PENCOM/ITF/NSITF, NDPC licence, ISO 27001/22301, performance bond — document checklist implemented, registry verification seam** | Corporate + bid confidentiality | TigerBeetle settlement (101/103); document stores | ICRC Act 2005; state PPP laws; QCBS scorecard doc | Postgres, TigerBeetle | IMPLEMENTED (reference) | CAC/FIRS/PENCOM verification adapters (F-023 dependency) |
| F-045 | PPP | Concession monitoring: milestones, append-only monthly KPI records, revenue-share reconciliation statements, step-down schedules | `services/mod-ppp-investment/app/service.py` | Concessionaires, state monitors, auditors | Contract award → concession ops | KYB (as F-044) | Financial + performance | TigerBeetle | Concession agreements; ICRC documentation from day one | TigerBeetle | IMPLEMENTED (reference) | Automated KPI ingestion from modules |
| F-046 | PPP | Procurement-integrity hash-chained audit + `GET /audit/verify` | `services/mod-ppp-investment/app/repo.py` | Auditors, ICRC | n/a | n/a | Audit | OpenSearch archive — seam | ICRC compliance | OpenSearch | IMPLEMENTED (reference) | Archive sink |

### 1.8 Data platform, documents & cross-cutting

| # | Domain | Feature | Implementation path | User / stakeholder | Onboarding path | KYC/KYB requirement | Data class | Integration / adapter | Legal / policy gate | Infra dependency | Status | Next requirement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-047 | Lakehouse | Medallion pipeline (Bronze ingest → Silver normalize/dedupe/quarantine → Gold daily IGR by state) | `services/lakehouse/lakehouse/medallion.py`, tests | Data engineers, analysts | n/a | n/a | Revenue events (de-identified) | Delta Lake on MinIO, Flink/Spark — ADAPTER-SEAM (pandas reference) | Streaming < 2 s zero-loss acceptance | MinIO, Flink/Spark | PARTIAL | Flink/Spark jobs on Delta tables |
| F-048 | Lakehouse | ML stubs: delinquency scoring (deterministic interface for isolation-forest/graph-NN), AVM target (>92% R²) | `services/lakehouse/lakehouse/medallion.py` (`delinquency_scores`); AVM referenced by mod-gis-luc | Data scientists, revenue officers | n/a | n/a | Derived scores | Ray + MLflow — ADAPTER-SEAM | Model-governance TBD | Ray, MLflow | ADAPTER-SEAM | Train/deploy Ray models; model registry |
| F-049 | Documents | Document management, OCR & records archiving (WP-18: MinIO hierarchy, Tesseract/Trident OCR, signed PDF/A) | Architecture + WP-18 spec only; **Stage 4.3 adds `packages/document-ai/` (PaddleOCR/Docling adapters)** | MDAs, archivists, lands registry | Bulk digitization projects | n/a (documents may contain PII → object refs + hashes per sovereignty constraints) | Document images + extracted text (PII-bearing) | MinIO, OCR engines — ADAPTER-SEAM | 50-yr retention; < 2 s search acceptance | MinIO, GPU | **GAP** (adapter seams land in Stage 4.3) | Implement document-ai adapters; PDF/A signing |
| F-050 | Events | AsyncAPI event contracts on `ng.sos.*` (tenant lifecycle, settlement, mining, forestry, platform) | `contracts/asyncapi/*.yaml` | All services | n/a | n/a | Event envelopes | Kafka/Fluvio — ADAPTER-SEAM (in-memory buses in services) | Contracts-first rule (no merge w/o contract) | Kafka/Fluvio | PARTIAL | Schema registry; broker deployment |
| F-051 | Config | Per-state policy packs + module enablement (6 states × `policy-pack.json` + `modules.yaml`), validator | `config/states/*`, `config/states/validate_packs.py`; example pack `contracts/policy-packs/examples/ogun-luc-2026.json` | State administrators | State onboarding wave | n/a | Config | jsonschema validation (implemented); OPA hooks (seam) | Gazette references per pack | n/a | IMPLEMENTED | Expand guardrail schema coverage (guardrails beyond splits) |
| F-052 | Testing | Load test (k6 ledger split: 500k TPS TB, 50k req/s APISIX, p99 < 50 ms) | `tests/load/k6-ledger-split.js` | QA, acceptance auditors | n/a | n/a | n/a | k6 | Acceptance framework Stage 2 | k6, clusters | PARTIAL | Run against live clusters; add Sedona stress suite |
| F-053 | Testing | FAT/SAT/contract/security/sat suites | `tests/README.md` (planned dirs) | QA, acceptance auditors | n/a | n/a | n/a | — | Milestone payment gating | — | **GAP** | Build contract/security/sat suites |

---

## 2. Gap & adapter-seam summary (honest register)

### 2.1 Hard GAPs (no in-repo implementation)

| Gap | Impact | Planned resolution |
|---|---|---|
| **F-023 KYC/KYB capability** (OCR, Docling, VLM adjudication, liveness, KYB registry verification, risk scoring, review queues) | Blocks production onboarding of citizens (NIMC verification), agents, traders, artisanal miners, facilities, vendors; every KYC/KYB cell above depends on it | Stage 4.2 spec (`SPEC-KYC-KYB.md`) → Stage 4.3 `services/mod-kyc-kyb` + document-AI adapters |
| F-049 Document management/OCR archiving (WP-18) | Historical deed digitization unstarted | Stage 4.3 document-ai adapters are the reusable seam; full WP-18 pipeline later |
| F-053 Contract/security/SAT test suites | Acceptance gates 1/3/4 not executable | Backlog; k6 suite exists as template |
| Artisanal miner biometric registry (F-030 component) | Named in module scope; no biometric capture code | Depends on F-023 liveness/biometric seams |
| Civil-servant biometric capture pipeline (F-022 dependency) | Payroll clean-up runs on hashes only | Device + capture app; Temporal workflow binding |

### 2.2 Adapter seams (interface defined, production adapter pending)

| Seam | Defined in | Production target |
|---|---|---|
| TigerBeetle client | `ledger/splits` `LedgerClient`; `REV_CORE_LEDGER=tigerbeetle` stub | tigerbeetle-go adapter + clusters |
| Kafka/Fluvio bus | `mod-mining/app/bus.py` (`KafkaEventBus` documented); env/forestry bus params | aiokafka/Fluvio deployment |
| Temporal workflows | `mod-gis-lands/temporal_adapter.py`; citizen-portal `TemporalWorkflowRef` | Temporal server, per-state task queues |
| NIMC NIN verification | mod-identity / mod-citizen-portal READMEs (hash-at-rest implemented) | NIMC API integration |
| CAC / FIRS / PENCOM / ITF / NSITF verification | mod-ppp-investment DOC-01…08 checklist; rev-core STIN notes | KYB registry adapters (F-023) |
| Mojaloop FSPIOP / NIBSS e-Bills | WP-04; webhook stubs in rev-core/education/mobility | Mojaloop connectors + scheme certification |
| Hardware secure element signing | `edge/edge-daemon/edge_daemon/crypto.py` (`DeviceSigner` swap point) | Android SE-backed signer, Rust daemon |
| Sedona/DataFusion spatial jobs | `geospatial/sedona/*` + local twins | Cluster execution |
| Ray/MLflow ML | lakehouse stubs | Model training/serving |
| Wazuh XDR / OpenCTI / OpenSearch archive | READMEs (mod-environment, mod-police-cad, arch 06) | SOC deployment |
| Permify ReBAC | mod-identity README mapping | Permify deployment |
| PostGIS repositories | `lands_app/repository.py`, `luc_app/repository.py`, mod-mining `repo.py` stubs | Drop-in Postgres impls w/ RLS |
| Cowry Gen 2 card bridge | mod-mobility-switch stub | Cowry production integration |
| USSD collection | mod-market README | USSD gateway aggregator |
| Legacy GIS migration (NAGIS/BENGIS/TAGIS/LASGIS/OLARMS/OGIS) | mod-gis-lands README | ETL pipelines per state |

### 2.3 Legal / policy gates tracked in-repo

| Gate | Where encoded | State |
|---|---|---|
| State-police constitutional amendment (24/36 + assent; Ebubeagu precedent) | `mod-police-cad/app/gate.py` — **in code, defaults closed** | IMPLEMENTED (gate closed until certified tally) |
| Federal vs state mining revenue separation | `ledger/chart-of-accounts.md` class 5xxx; `mod-mining` `RoyaltyConstraintViolation` | IMPLEMENTED (by construction) |
| Concession revenue-share ceilings (≤8% Lagos/Ogun, ≤15% others) | `config/states/README.md`; procurement guardrails | Documented; enforcement via policy-pack validation — PARTIAL |
| NDPA 2023 (residency, minimization, consent, revocation) | mod-identity (in code + tests); arch 06; compliance.md | PARTIAL (code-level minimization implemented; residency = infra) |
| Clause 22.2 revenue non-negotiables (CRF/TSA direct, no vendor escrow of gross) | `ledger/README.md` | Documented; enforced by split engine design |
| Gazette anchoring of every policy pack / fare table / corridor limit | `config/states/*`, mod-mobility-switch, mod-transport-wim | IMPLEMENTED (field enforced in schemas) |

---

## 3. Stakeholder → feature cross-reference

| Stakeholder | Primary features | Onboarding doc section |
|---|---|---|
| Citizens / residents | F-004, F-017–F-021, F-040, F-041 | `docs/governance/stakeholder-onboarding.md` §Citizens |
| Civil servants | F-004, F-022 | §Civil servants |
| MDA staff | F-004, F-020, F-021, F-024–F-027, F-040, F-041 | §MDA staff |
| POS / field agents | F-015, F-037, F-039 | §POS & field agents |
| Market traders | F-037 | §Market traders |
| Miners (licensed + artisanal) | F-030, F-031 | §Miners |
| Transporters / haulage | F-031, F-036, F-038 | §Transporters |
| Facilities (industrial/health/education) | F-033–F-035, F-040, F-041 | §Facilities |
| Vendors / concessionaires | F-044, F-045, F-019 (API consumers) | §Vendors & concessionaires |
| Auditors | F-003, F-016, F-018, F-046, F-052 | §Auditors |
| Administrators (platform + state) | F-001–F-010, F-051 | §Administrators |
| Corporate entities (taxpayers, miners, developers) | F-013, F-030, F-034, F-044 | §Corporate entities |

> Detailed onboarding, verification, credentialing, suspension, and audit flows per
> stakeholder class are defined in
> [`docs/governance/stakeholder-onboarding.md`](../governance/stakeholder-onboarding.md).

## 4. Provenance & method

- Source: full-tree scan of this branch (2026-09-06); service READMEs, module code,
  `contracts/`, `db/migrations/`, `config/states/`, `infra/`, `edge/`, `ledger/`,
  `geospatial/`, `tools/`, `tests/`, and `docs/architecture/*`, `docs/delivery/*`,
  `docs/governance/*`, `docs/procurement/*`, `docs/ppp-pipeline/*`.
- `docs/delivery/backlog/feature-catalog.md` (FEAT-001…035) is a **verbatim port of the
  generic SAFe workbook template**, not SOS-specific; it is retained for provenance but is
  superseded by this register.
- Status values reflect runnable code + tests vs documented seams, per the vocabulary above.
