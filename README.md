# State Operating System (SOS) — Unified Multi-Tenant Sovereign Platform

> **Nigeria's first unified, multi-tenant digital governance and automated revenue infrastructure for subnational governments.**
> One common operating core. Six pilot states. Thirty ranked Technology-PPP opportunities. Zero upfront State CapEx.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Governance](https://img.shields.io/badge/Governance-ICRC%202005%20%C2%B7%20NDPA%202023%20%C2%B7%20NITDA-green)](docs/governance/compliance.md)
[![Architecture](https://img.shields.io/badge/Architecture-Multi--Tenant%20Sovereign%20Cloud-orange)](docs/architecture/01-architecture-blueprint.md)

---

## What is SOS?

The **State Operating System (SOS)** is a production-grade, multi-tenant digital governance and revenue infrastructure that provides a single common operating core deployable across Nigerian states. Every high-value economic opportunity — lithium mining custody, cadastral land titling, transit ticketing, weigh-in-motion corridors, market levies — is provisioned as an application module on top of sovereign shared infrastructure.

**Canonical Platform Concept:** each State Government operates as an isolated sovereign tenant; each Ministry, Department, and Agency (MDA) operates as a sub-tenant. State-specific tax laws, concession splits, cadastral fee schedules, and ministry workflows are injected dynamically through **declarative State Policy Packs (JSON/OPA Rego)** — the core codebase is identical across all states (**80/20 rule: ≥80% shared configuration, ≤20% per-state customization**).

### The Six-State Spectrum

| State | Population | Baseline IGR | Primary Anchor | Tenancy Tier |
|---|---|---|---|---|
| **Lagos** | 22.0M+ | ₦1.26 Trillion | Financial, commercial & multimodal logistics hub | Tier 1 — Dedicated Sovereign |
| **Ogun** | 6.5M | ₦146.8 Billion | Industrial & manufacturing corridors | Tier 2 — Hybrid |
| **Osun** | 4.7M | ₦28.4 Billion | Artisanal gold, cocoa, heritage tourism | Tier 3 — Shared Multi-Tenant |
| **Benue** | 6.2M | ₦24.1 Billion | Food basket, river logistics | Tier 3 — Shared Multi-Tenant |
| **Nasarawa** | 2.9M | ₦20.5 Billion | Lithium / solid minerals, Abuja-border sprawl | Tier 3 — Shared Multi-Tenant |
| **Taraba** | 3.6M | ₦17.46 Billion | Highland agribusiness, rosewood, cross-border trade | Tier 3 — Shared Multi-Tenant |

### The SOS Module Suite

30 PPP opportunities reduce to **reusable modules** — built once, configured per state. v3.0 extends the original seven-suite catalog with **ENV-09** (environment & carbon), **CIT-11** (unified citizen portal), and **KYC-KYB** (sovereign identity verification):

| Module | Serves | Core Stack |
|---|---|---|
| **(a) Revenue Core** (`mod-rev-core`) | IGR e-collection, POS/USSD/agent networks, e-TCC, billing engine | Go · Postgres · TigerBeetle |
| **(b) Land & Property** (`mod-gis-lands`, `mod-gis-luc`) | GIS cadastre, e-C-of-O workflows, ground rent / LUC billing | PostGIS · Apache Sedona · Temporal |
| **(c) Market & Levy** (`mod-market`, `mod-agri-waybill`) | Markets, transport parks, haulage e-waybills, weighbridges | Go · Dapr · Redis |
| **(d) Extractives** (`mod-mining`, `mod-forestry`) | Mining formalization, ASM buying centres, timber provenance | Rust · Kafka · PostGIS |
| **(e) Security & Safety** (`mod-police-cad`) | C2, CCTV/drone integration, force HR/payroll, trust-fund administration | Rust · PostGIS · OpenCTI |
| **(f) Identity & Data** (`mod-identity`) | Resident registry, verification APIs (LASRRA/QoreID model) | Keycloak · APISIX metered gateway |
| **(g) PPP & Investment** (`mod-ppp-investment`) | Pipeline disclosure, OBC/FBC workflow, concession monitoring | Temporal · OpenSearch |
| **(h) Environment & Carbon** (`mod-environment`) — *v3.0 / ENV-09* | Industrial emissions telemetry compliance, effluent/timber permits, deforestation surveillance (<4h SLA), carbon registry, EIA | FastAPI · PostGIS · Sedona |
| **(i) Citizen Portal** (`mod-citizen-portal`) — *v3.0 / CIT-11* | Citizen identity wallet & Keycloak SSO, multi-MDA self-service, e-petitions, payroll ghost-worker audit | FastAPI · Keycloak OIDC · Temporal |
| **(j) KYC/KYB Verification** (`mod-kyc-kyb`) — *v3.0 / KYC-KYB* | Tenant-isolated KYC/KYB case management, document AI extraction (PaddleOCR/Docling/VLM adjudication), liveness anti-spoof, registry verification seams, risk scoring, hash-chained audit | FastAPI · PostGIS RLS · object-store refs/hashes |

### Architecture at a Glance — Four Layers

1. **Layer 1 — Unified Perimeter, Security & Sovereign IAM:** Apache APISIX Gateway + OpenAppSec ML-WAF · Keycloak Multi-Realm IAM (federated with NIMC NIN and CAC) · Wazuh XDR + OpenCTI SOC.
2. **Layer 2 — Real-Time Financial Ledger & Interoperable Clearing:** TigerBeetle 120-bit double-entry kernel (>1M TPS) · Mojaloop ISO 20022 payment switch (NIBSS, Remita, Interswitch) · Temporal durable workflows.
3. **Layer 3 — Distributed Geospatial Intelligence & Lakehouse:** Apache Sedona + SedonaDB (0.24s spatial joins) · PostGIS OLTP · Delta Lake + Apache Flink + Ray (Bronze-Silver-Gold medallion, AI valuation).
4. **Layer 4 — Modular Subnational Application Suite:** 20+ application modules provisioned per state via policy packs.

Full detail: [`docs/architecture/`](docs/architecture/README.md).

## Commercial & Governance Model

- **Zero-CapEx vendor-financed PPP concession** (5–7 year BOT): concessionaire finances 100% of deployment in exchange for a performance-indexed **8–18% share of incremental IGR**.
- **Programmatic statutory revenue split:** every payment is split atomically at the ledger level (TigerBeetle) into State Consolidated Revenue Fund (TSA), MDA retention, and concessionaire escrow — *no vendor ever touches un-split gross state funds*.
- **Compliance:** ICRC Act 2005 · State PPP Laws · Nigeria Data Protection Act (NDPA) 2023 (in-country residency) · NITDA local-content guidelines.
- **Anti-lock-in:** permissive FOSS core (MIT/Apache-2.0), quarterly source-code escrow, open OpenAPI 3.1 contracts, data exportable in open formats.

## Repository Map

| Path | Contents |
|---|---|
| [`docs/`](docs/README.md) | Architecture, ADRs, delivery, procurement, state profiles, PPP pipeline, governance |
| [`services/`](services/README.md) | Core microservice modules (`mod-*`) + control-plane |
| [`contracts/`](contracts/README.md) | OpenAPI 3.1, AsyncAPI event contracts, policy-pack schemas |
| [`db/`](db/README.md) | PostgreSQL/PostGIS DDL migrations with Row-Level Security |
| [`ledger/`](ledger/README.md) | TigerBeetle chart-of-accounts & atomic split reference implementation |
| [`geospatial/`](geospatial/README.md) | Apache Sedona distributed spatial jobs |
| [`infra/`](infra/README.md) | Kubernetes, Helm, Terraform, ArgoCD GitOps per tenancy tier |
| [`config/states/`](config/states/README.md) | Per-state dynamic policy packs (6 pilot states) |
| [`edge/`](edge/README.md) | Offline-first POS / checkpoint edge design |
| [`tests/`](tests/README.md) | Load, FAT/SAT and acceptance specs mapped to procurement gates |
| [`tools/`](tools/README.md) | `sosctl` tenant-provisioning CLI |
| [`deploy/`](deploy/README.md) | Local development stack (Docker Compose: PostGIS, TigerBeetle, Keycloak, Redpanda, MinIO, OpenSearch + flagship and v3.0 services) |
| [`Makefile`](Makefile) | `make test`, `make validate`, `make contracts`, `make compose-up` |
| [`.github/`](.github/) | CI, security scanning, SBOM, issue & PR templates |

The full design rationale is recorded in [`docs/REPO-STRUCTURE.md`](docs/REPO-STRUCTURE.md).

## Delivery Roadmap (24 Months, 4 Waves)

| Wave | Window | Focus | Gate |
|---|---|---|---|
| **Wave 0** | M1–M3 | Foundations & pilots — control plane, Keycloak, TigerBeetle kernel; Lagos transit, Ogun WIM, Nasarawa lithium pilots | TSA integration sign-off |
| **Wave 1** | M4–M8 | Revenue acceleration — `mod-rev-core`, cadastral GIS & e-C-of-O, minerals & forestry provenance | ₦10B cleared on ledger |
| **Wave 2** | M9–M15 | Sector expansion — agribusiness e-taxation, waterways, tertiary & hospital billing | Sedona spatial join live |
| **Wave 3** | M16–M24 | Advanced scale & AI — cross-border trade, Ray AI land valuation, federated lakehouse | 30 modules fully live |

Details: [`docs/delivery/rollout-matrix.md`](docs/delivery/rollout-matrix.md) and the [90-day execution playbook](docs/delivery/90-day-playbook.md).

## Data-Honesty & Provenance Framework

Every material data point carries a mandatory provenance tag:

- **[LIVE]** — verified fact drawn from official state budgets, NBS data, JTB published returns, or gazetted state laws.
- **[DERIVED]** — analytical projection calculated from verified baselines and comparable subnational concession benchmarks.
- **[GAP]** — an identified data vacuum / documented absence that itself constitutes the commercial opportunity.

## Contributing & Security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). All dependencies must use permissive licenses (MIT, Apache-2.0, BSD, ISC, PostgreSQL); copyleft requires written approval. Every deliverable ships a signed SBOM (CycloneDX v1.5 / SPDX v2.3).

---

*SOS Consortium & Delivery PMO · September 2026 · Prepared for State Governors, Commissioners of Finance & Science-Tech, PPP Units, Institutional Investors & DFIs.*
