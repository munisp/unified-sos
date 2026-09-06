# SOS v3.0 Business Specification (Aligned Port)

**Source:** *State Operating System (SOS) — Comprehensive Business Specification & Technical Requirements Pack*, v3.0.0, plus the geospatial research summary. This document is a concise but complete port of the v3.0 pack's sections 1–8. Provenance tags follow `docs/governance/`: **[LIVE]** verified fact · **[DERIVED]** analytical projection · **[GAP]** documented absence.

> Where a v3.0 figure could not be re-verified against a primary source inside this repository, it is tagged **[DERIVED]** or **[GAP]** rather than asserted as live.

---

## Section 1 — Executive Vision

The State Operating System (SOS) is a shared digital public-infrastructure stack for Nigerian subnational governments: one canonical codebase, configured per state (the 80/20 rule — shared module code; state variance lives in `config/states/` policy packs, never per-state forks). The v3.0 pack positions SOS as the operating system for every major state revenue, service-delivery and enforcement domain, organized into **12 business domains (REV-01 … PPP-12)** with a unified financial core, a geospatial/lakehouse intelligence layer, and a zero-capex PPP concession commercial model.

Headline v3.0 targets **[DERIVED]** (projections from the pack; not independently verified here):

- Material uplift of state IGR through billing digitization, cadastre/LUC coverage and leakage closure across all six launch states.
- Citizen-portal coverage >80% of state services, CSAT >90%.
- ₦500m+ recoverable savings from civil-service ghost-worker clean-up (CIT-11).
- <4h response from satellite deforestation detection to dispatched enforcement (ENV-09 → SEC-10).

## Section 2 — State Tenancy Spectrum & IGR Baseline

Six launch states on a three-tier tenancy spectrum (live config in `config/states/<state>/modules.yaml`; per-state profiles in `docs/states/`):

| Tier | States | Platform posture |
|---|---|---|
| `dedicated` | Lagos | Own control-plane slice; rollout wave 0/1 |
| `hybrid` | Ogun | Shared control plane, dedicated data-plane elements |
| `shared` | Osun, Benue, Nasarawa, Taraba | Shared multi-tenant platform |

v3.0 state profile / IGR positioning **[DERIVED]** (directional summary of the pack's state spectrum; precise baseline IGR figures are **[GAP]** until re-verified against state audited accounts):

| State | Dominant v3.0 plays | v3.0 modules emphasized |
|---|---|---|
| Lagos | High-density cadastre/LUC, PSP billing, transit clearing, port corridor, industrial emissions | REV-01, LND-02, TRN-05, SEC-10, ENV-09 |
| Ogun | Industrial corridor titling, WIM freight, commuter-belt LUC, emissions compliance | LND-02, TRN-05, EXT-03, ENV-09 |
| Osun | Markets, education billing, gold-mining levies, shared-config environment | MKT-06, EDU-08, EXT-03, ENV-09 |
| Benue | Agri waybills/warehouse receipts, market digitization, sand-mining royalties | AGR-04, MKT-06, EXT-03, ENV-09 |
| Nasarawa | Mining custody, health billing, agro-hub receipts | EXT-03, HLT-07, AGR-04, ENV-09 |
| Taraba | Forest surveillance, tea/agri traceability, minerals, timber permits | ENV-09, EXT-03, AGR-04, LND-02 |

CIT-11 (citizen portal/identity/civil service) and the financial core (PPP-12 waterfall) deploy in **all six states**.

Every tenant-owned object carries `tenant_state_id` ∈ {lagos, ogun, osun, benue, nasarawa, taraba}; services reject cross-tenant access, and Postgres Row-Level Security keyed on `app.current_state_tenant` enforces isolation at the database layer (`db/migrations/`).

## Section 3 — 12-Domain Module Catalog (v3.0 codes)

| Code | Domain (v3.0 title) | Implementing module(s) | Status |
|---|---|---|---|
| REV-01 | Revenue administration (STIN, assessment, billing, POS sync) | `mod-rev-core` | IMPLEMENTED |
| LND-02 | Land: cadastre, e-C-of-O titling, LUC satellite valuation | `mod-gis-lands`, `mod-gis-luc` | IMPLEMENTED |
| EXT-03 | Extractives: mining custody/weighbridge levies, forestry provenance | `mod-mining`, `mod-forestry` | IMPLEMENTED |
| AGR-04 | Agriculture: produce e-waybills, warehouse receipts | `mod-agri-waybill` | IMPLEMENTED |
| TRN-05 | Transport: weigh-in-motion, ANPR, multimodal transit clearing | `mod-transport-wim`, `mod-mobility-switch` | IMPLEMENTED |
| MKT-06 | Markets: stall cadastre, micro-tenancy billing | `mod-market` | IMPLEMENTED |
| HLT-07 | Health: hospital billing, SHIA/NHIS claims | `mod-health` | IMPLEMENTED |
| EDU-08 | Education: tertiary consolidated billing & bursary | `mod-education` | IMPLEMENTED |
| ENV-09 | Environment: emissions telemetry, permits, deforestation surveillance, carbon registry, EIA | `mod-environment` | NEW in v3.0 |
| SEC-10 | Safe City / security: 112 CAD dispatch, patrol geofencing | `mod-police-cad` | IMPLEMENTED (ratification-gated) |
| CIT-11 | Citizen Portal, Sovereign Identity SSO & Civil Service clean-up | `mod-citizen-portal` | NEW in v3.0 |
| PPP-12 | Concession & revenue waterfall: PPP pipeline, escrowed statutory splits | `mod-ppp-investment`, `config/states/*/policy-pack.json` | IMPLEMENTED |

Full requirement-level traceability: `docs/delivery/rtm-v3.md`.

## Section 4 — Geospatial & Lakehouse Architecture

Dual-engine spatial architecture (ADR-004): **PostGIS** for OLTP, **Apache Sedona** for planetary-scale analytics. v3.0 additions **[DERIVED]**:

- **SedonaDB** (Rust/DataFusion embedded engine) for single-node spatial analytics at the lakehouse edge.
- **H3 hexagonal indexing** for deforestation alert cells and telemetry geofencing.
- **GeoParquet / Delta Lake WKB** as the Silver→Gold lakehouse spatial storage format.
- State GIS agency integration seams: **NAGIS** (Nasarawa), **TAGIS** (Taraba), **LASGIS** (Lagos), **BENGIS** (Benue) — no live agency feed connected in this repo **[GAP]**.
- v3.0 benchmark (10M-row join: R-tree ~6.4s vs SedonaDB ~0.24s, ~26×) tagged **[DERIVED]** in `geospatial/README.md` pending a reproducible harness.

## Section 5 — Financial Core

- Money is integer **kobo** everywhere; balances are never mutated in Postgres — the TigerBeetle ledger is the balance source of truth; Mojaloop clearing is an adapter seam.
- Deterministic settlement waterfall (PPP-12): statutory split legs reference ledger account codes — `3001` state CRF/TSA, `2099` PPP concessionaire escrow, optional MDA/LGA codes (`ledger/chart-of-accounts.md`); federal pass-through `5001` is never credited by state splits.
- Splits are governed by `config/states/<state>/policy-pack.json` with concession ceiling guardrails (8% Lagos/Ogun, 15% agrarian/extractive states) enforced by `config/states/validate_packs.py` **[LIVE]**.
- ENV-09 revenue model **[DERIVED]**: effluent discharge permit fees, carbon-credit brokerage, timber/logging royalties, violation fines (state fine multipliers).
- CIT-11 fee settlement default split **[DERIVED]**: 70% state / 15% MDA / 15% platform on smartcard and expedited-service fees, overridable per state config.

## Section 6 — Open-Source Stack Additions (v3.0)

| Component | Role |
|---|---|
| Apache Sedona / SedonaDB (Rust + DataFusion) | Spatial analytics engine (cluster + embedded) |
| H3 | Hexagonal spatial indexing for alerts/telemetry |
| GeoParquet + Delta Lake | Lakehouse spatial storage (WKB geometry) |
| Keycloak (multi-realm OIDC) | Sovereign identity SSO per state tenant (CIT-11) |
| Temporal | Durable workflows (EIA, payroll verification, titling) |
| Kafka / Fluvio | Event streaming for telemetry and lifecycle events |
| Wazuh | Security monitoring / SIEM seam (ENV-09, CIT-11) |
| TorchGeo + Sentinel-2/Landsat COGs | NDVI change detection |

## Section 7 — Commercial Models, Legal Prerequisites & Personas

**Commercial models** (per `docs/ppp-pipeline/`, `docs/procurement/`):

1. **Zero-capex technology concession** — vendor funds delivery; repayment via escrowed revenue share (ceiling 8% Lagos/Ogun, 15% agrarian/extractive states) **[LIVE guardrail]**.
2. **Transaction-fee legs** on settlement flows (PPP-12 waterfall) — per-bill deterministic splits confirmed by real-time webhook.
3. **Module SaaS-equivalent yield** — per-domain recurring platform fees embedded in policy packs.
4. **v3.0 additions [DERIVED]** — carbon-credit brokerage (ENV-09), expedited citizen-service fees (CIT-11), enforcement fine share (SEC-10/ENV-09).

**Legal prerequisites** (Zero-Capex Concession Structuring Gate): executed PPP Concession Agreement, gazetted statutory revenue-split order, approved NDPA Data Protection Compliance Statement **[LIVE]**. v3.0 adds: state environmental-agency enablement + carbon-market registry rules for ENV-09 **[GAP — instruments to be gazetted per state]**, and NIMC integration agreement + civil-service biometric enrollment mandate for CIT-11 **[GAP]**.

**Personas:** Governor/Executive Sponsor; SIRS Chairman and MDA revenue officers (REV-01); land registry & GIS agency operators (LND-02; NAGIS/TAGIS/LASGIS/BENGIS); mining marshals & forestry officers (EXT-03); produce merchants (AGR-04); transport authorities (TRN-05); market associations (MKT-06); hospital & bursary administrators (HLT-07, EDU-08); environmental enforcement officers & carbon project developers (ENV-09); police/Safe-City dispatchers (SEC-10); citizens and civil-service payroll auditors (CIT-11); PPP concessionaire operations teams (PPP-12).

## Section 8 — Roadmap, SLA & RTM

**4-phase roadmap** (aligned to the repo's five release trains RT-01…RT-05 over a 24-month horizon and 4-wave rollout; phase labels per the v3.0 pack) **[DERIVED]**:

| Phase | Horizon | Focus |
|---|---|---|
| Phase 1 — Foundation | Months 0–6 | Control plane, tenancy, financial core, REV-01/LND-02 in Lagos & Ogun; legal gate instruments gazetted |
| Phase 2 — Revenue Expansion | Months 6–12 | EXT-03, MKT-06, HLT-07, EDU-08 across shared-tier states; SEC-10 ratification-gated |
| Phase 3 — v3.0 Domains | Months 9–18 | ENV-09 (Lagos/Ogun industrial, Taraba forest) and CIT-11 (all six states) rollout; carbon registry live |
| Phase 4 — Scale & Optimize | Months 18–24 | AGR-04/TRN-05 deepening, lakehouse AI optimization, cross-state benchmarks, exit/handover options |

**SLA highlights:** deforestation alert response <4h from detection to dispatched SEC-10 enforcement ticket; settlement webhook confirmation in real time; zero cross-tenant leakage pen-test gate (REQ-SEC-01); module availability/penalty regime per `docs/procurement/` SLA/SLO schedules.

**RTM:** requirement-to-repo traceability lives in `docs/delivery/rtm-v3.md`.
