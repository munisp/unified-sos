# Land Management Gap Analysis — munisp/landmanagement vs Unified SOS

Date: 2026-09-12 · Source repo reviewed: `github.com/munisp/landmanagement` (IDL­R Property Title System, "IDL­R-PTS")

## 1. What the landmanagement repo is

A TypeScript monorepo (React 19 + Express/tRPC + Drizzle/Postgres + Redis) with Go and Python
microservices, pitched as a standalone national land registry:

- **Domain**: parcel registry, title transfer workflows, document management with OCR
  (PaddleOCR + VLM + Docling), Hyperledger Fabric blockchain anchoring, escrow, mortgage
  suite (application, broker, insurance, refinancing, secondary market, loan pooling),
  tax ops (FIRS), disputes, right-of-way, drone imagery (OpenDroneMap), GeoAI/Sedona,
  government integrations (NIMC, CAC, NIPOST, NIBSS, FIRS), 9-language i18n.
- **Claimed status**: 72/105 features (69%); many advanced items marked 🔄 in-progress.
- **Architecture**: monolithic tRPC server (~200 service/repository files) + sidecar
  microservices; Istio, Helm, ELK, Prometheus/Grafana.

## 2. Platform land capabilities before this stage

| Capability | Module | Depth |
|---|---|---|
| Cadastre parcels, registration, search | `mod-gis-lands` | DEEP |
| e-C-of-O titling workflow (6-stage → Governor consent) | `mod-gis-lands` titling state machine, Temporal-ready | DEEP |
| Deed verification, Ed25519 digital signing | `mod-gis-lands` signing | DEEP |
| SLA tracking per titling stage | `mod-gis-lands` sla | DEEP |
| Land Use Charge calculation, tariffs, ingestion | `mod-gis-luc` | DEEP |
| Automated valuation model (PyTorch `luc_avm`) | `ml/` + `mod-ml-inference` | DEEP (trained weights) |
| Geospatial backbone (PostGIS, Sedona, GeoLibre, H3→lakehouse) | `mod-geospatial` | INTEGRATED (fail-closed seams) |
| 3D cadastre visualization (CesiumJS) | `apps/dispatch-console` Cadastre3D | INTEGRATED |

## 3. Gap analysis

| # | landmanagement feature | Platform status | Verdict / action |
|---|---|---|---|
| 1 | Parcel registration/search | ✅ exists, deeper (survey-grade signing, state tenant scoping) | Platform ahead |
| 2 | Title transfer workflow | ✅ exists (e-C-of-O 6-stage, governor consent, SLA) | Platform ahead |
| 3 | Parcel **subdivision / merger** | ❌ absent | **IMPLEMENTED** (area-conserving, lineage, dispute/workflow guards) |
| 4 | **Ownership history / chain-of-title** API | ⚠️ implicit only | **IMPLEMENTED** |
| 5 | **Dispute management** (boundary/ownership/fraud) | ❌ absent | **IMPLEMENTED** (lifecycle + blocks titling/subdivision while open) |
| 6 | **Title risk scoring** (fraud signals) | ⚠️ fraud_gnn exists, not wired to land | **IMPLEMENTED** (risk adapter → `mod-ml-inference`, deterministic fixture) |
| 7 | **Blockchain anchoring** of titles | ⚠️ hash-chained audit only (by design) | **IMPLEMENTED** as merkle-chain anchor seam (`SOS_LANDS_ANCHOR_URL`); Fabric deliberately not adopted — hash-chain + TigerBeetle give the same integrity without the ops burden |
| 8 | **Document management + OCR** (deeds, survey plans, C-of-O) | ❌ absent | **IMPLEMENTED** as `mod-land-docs` (lifecycle, classification, versioning, duplicate detection, PaddleOCR/Docling-ready seam, fail-closed) |
| 9 | **Mortgage/lien suite** | ❌ absent | **IMPLEMENTED** as `mod-mortgage` (credit scoring via `credit_mlp` seam, lien registry with priority, two-phase disbursement via fundsflow semantics, annuity schedule in integer kobo, discharge/default/foreclosure) |
| 10 | Escrow | ⚠️ PPP escrow accounts exist in fundsflow (2099) + agri-trace pledges | Sufficient; mortgage disbursement reuses hold→post two-phase |
| 11 | Payments: Paystack/Flutterwave | ⚠️ platform uses Mojaloop/NIBSS + TigerBeetle (system of record) | Platform pattern superior for gov IGR; not adopted |
| 12 | FIRS tax integration | ⚠️ LUC + `mod-rev-core` cover state taxation; FIRS seam via `mod-kyc-kyb`/TIN | Existing coverage adequate |
| 13 | NIMC/CAC/NIPOST/NIBSS verification | ✅ `mod-kyc-kyb`, `mod-identity`, `mod-mobility-switch` seams | Platform ahead |
| 14 | Drone imagery / OpenDroneMap | ⚠️ drone *streams* (WebRTC, safecity); no photogrammetry | Accepted gap: ODM is a batch pipeline — candidate for lakehouse job, not a service |
| 15 | GeoAI / Sedona | ✅ `mod-geospatial` Sedona adapter + lakehouse H3 | Platform equivalent |
| 16 | 9-language i18n | ⚠️ 4 locales (en/yo/ha/ig) in whitelabel branding | Deliberate scope: Nigerian majors only |
| 17 | Monolith tRPC architecture | — | Not adopted: platform's per-module FastAPI + tenant scoping + fail-closed seams is stricter |

## 4. What was implemented (Stage 13)

| Deliverable | Location | Tests |
|---|---|---|
| Land document + OCR service | `services/mod-land-docs/` | ≥25 |
| Mortgage & lien service | `services/mod-mortgage/` | ≥30 |
| Lands extensions: subdivision/merger, disputes, history, risk, anchoring | `services/mod-gis-lands/lands_app/` (+`test_lands_extensions.py`) | ≥28 |
| Wiring: contracts (OpenAPI/AsyncAPI), policy-pack module list, compose/Helm/prometheus, scorecard | repo-wide | regen + gates |

All new code follows platform invariants: X-State-Tenant scoping (400/404), fail-closed
external seams (fixture in dev, boot failure under `SOS_*_PROFILE=production`), integer
kobo, hash-chained audit, deterministic fixtures, `/healthz` + `/metrics`.

## 5. Honest residuals (external, not code)

- PaddleOCR/Docling deployment + Nigerian deed corpus tuning (production OCR).
- Credit bureau scoring against real bureau data (currently `credit_mlp` synthetic-trained baseline).
- ODM photogrammetry pipeline (future lakehouse batch job).
- If a state mandates Hyperledger Fabric specifically, the anchor seam (`SOS_LANDS_ANCHOR_URL`)
  is the integration point — Fabric would sit behind it without domain changes.
