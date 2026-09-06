# SOS v3.0 Business Specification (Aligned Port)

**Source:** *State Operating System (SOS) — Comprehensive Business Specification & Technical Requirements Pack*, v3.0.0, plus the geospatial research summary. This document ports v3.0 sections 1–8 into repo documentation. Provenance tags follow `docs/governance/`: **[LIVE]** verified fact · **[DERIVED]** analytical projection · **[GAP]** documented absence.

> Where a v3.0 figure could not be re-verified against a primary source inside this repository, it is tagged **[DERIVED]** or **[GAP]** rather than asserted as live.

---

## 1. Executive Vision

The State Operating System (SOS) is a shared digital public infrastructure stack for Nigerian subnational governments: one canonical codebase, configured per state (the 80/20 rule — shared module code; state variance lives in `config/states/` policy packs, never per-state forks).

v3.0 extends the platform from its v2.x revenue/cadastre core into a full-spectrum operating system covering **12 business domains**, adding:

- **ENV-09 — Environmental Protection, Carbon Registry & Industrial Emissions** (`services/mod-environment/`): industrial IoT telemetry compliance, effluent/timber permits, deforestation satellite surveillance with a <4h response SLA, carbon credit registry (issue/transfer/retire), and EIA workflow.
- **CIT-11 — Unified Citizen Portal, Sovereign Identity SSO & Civil Service Clean-Up** (`services/mod-citizen-portal/`): citizen identity wallet over Keycloak OIDC multi-realm SSO (NIMC API seam), multi-MDA self-service catalog, e-petitions, and biometric payroll audit with ghost-worker detection.

Vision targets **[DERIVED]** unless re-verified: citizen-portal coverage >80%, ₦500m+ recoverable ghost-worker savings, CSAT >90%, <4h deforestation alert response.

## 2. Tenancy Spectrum

Six launch states on a three-tier tenancy spectrum (unchanged from v2.x; see `config/states/<state>/modules.yaml` and `docs/states/`):

| Tier | States | Notes |
|---|---|---|
| `dedicated` | Lagos | Own control-plane slice, wave 0/1 |
| `hybrid` | Ogun | Shared control plane, dedicated data plane elements |
| `shared` | Osun, Benue, Nasarawa, Taraba | Shared multi-tenant platform |

Every tenant-owned object carries `tenant_state_id` ∈ {lagos, ogun, osun, benue, nasarawa, taraba}; services must reject cross-tenant access, and Postgres Row-Level Security keyed on `app.current_state_tenant` enforces isolation at the database layer (`db/migrations/`).

## 3. 12-Domain Module Catalog (v3.0)

| Code | Domain | Module | Status |
|---|---|---|---|
| REV-01 | Revenue administration (STIN, assessment, billing) | `mod-rev-core` | IMPLEMENTED |
| GIS-02 | Cadastre & e-C-of-O titling | `mod-gis-lands` | IMPLEMENTED |
| GIS-03 | Land Use Charge satellite valuation | `mod-gis-luc` | IMPLEMENTED |
| MIN-04 | Mineral custody & weighbridge levies | `mod-mining` | IMPLEMENTED |
| FOR-05 | Timber provenance & forestry | `mod-forestry` | IMPLEMENTED |
| TRN-06 | Transport WIM & mobility switch | `mod-transport-wim`, `mod-mobility-switch` | IMPLEMENTED |
| AGR-07 | Agri produce e-waybills | `mod-agri-waybill` | IMPLEMENTED |
| HLT-08 | Health billing & claims | `mod-health` | IMPLEMENTED |
| EDU-09 | Education consolidated billing | `mod-education` | IMPLEMENTED |
| MKT-10 | Market micro-tenancy billing | `mod-market` | IMPLEMENTED |
| ENV-09 | Environment, carbon registry, emissions | `mod-environment` | NEW in v3.0 |
| CIT-11 | Citizen portal, SSO, payroll clean-up | `mod-citizen-portal` | NEW in v3.0 |
| SEC-10 | Safe-city CAD & enforcement dispatch | `mod-police-cad` | IMPLEMENTED (consumes ENV-09 enforcement tickets) |
| PPP-12 | PPP investment pipeline | `mod-ppp-investment` | IMPLEMENTED |

(The v3.0 pack numbers its requirement series REV-01…PPP-12 across the 12 domains; full traceability is in `docs/delivery/rtm-v3.md`.)

## 4. Geospatial & Lakehouse Architecture

Dual-engine spatial architecture (ADR-004): **PostGIS** for OLTP, **Apache Sedona** for planetary-scale analytics. v3.0 adds **[DERIVED]**:

- **SedonaDB** (Rust/DataFusion embedded engine) for single-node spatial analytics at the lakehouse edge.
- **H3 hexagonal indexing** for deforestation alert cells and telemetry geofencing.
- **GeoParquet / Delta Lake WKB** as the Silver→Gold lakehouse storage format.
- Integration seams with state GIS agencies: **NAGIS** (Nasarawa), **TAGIS** (Taraba), **LASGIS** (Lagos), **BENGIS** (Benue).

Details and the v3.0 benchmark table are in `geospatial/README.md`.

## 5. Financial Core

- Money is integer **kobo** everywhere; balances are never mutated in Postgres — the TigerBeetle ledger is the balance source of truth.
- Deterministic settlement splits reference ledger account codes: `3001` state CRF/TSA, `2099` PPP concessionaire escrow, optional MDA/LGA codes (`ledger/chart-of-accounts.md`); federal pass-through `5001` is never credited by state splits.
- ENV-09 revenue model **[DERIVED]**: effluent discharge permit fees, carbon-credit brokerage fee, timber/logging royalties, violation fines (state fine multipliers).
- CIT-11 settlement default split **[DERIVED]**: 70% state / 15% MDA / 15% platform on smartcard and expedited-service fees, overridable per state config.
- Statutory splits remain governed by `config/states/<state>/policy-pack.json` with the concession ceiling guardrails enforced by `config/states/validate_packs.py`.

## 6. Open-Source Stack Additions (v3.0)

| Component | Role |
|---|---|
| Apache Sedona / SedonaDB (Rust + DataFusion) | Spatial analytics engine (production + embedded) |
| H3 | Hexagonal spatial indexing for alerts/telemetry |
| GeoParquet + Delta Lake | Lakehouse spatial storage (WKB geometry) |
| Keycloak (multi-realm OIDC) | Sovereign identity SSO per state tenant |
| Temporal | Durable workflows (EIA, payroll verification, titling) |
| Kafka / Fluvio | Event streaming for telemetry and lifecycle events |
| Wazuh | Security monitoring / SIEM seam for ENV-09 & CIT-11 |
| TorchGeo + Sentinel-2/Landsat COGs | NDVI change detection |

## 7. Commercial Models, Legal Prerequisites & Personas

**Commercial models** (per `docs/ppp-pipeline/` and `docs/procurement/`): zero-capex technology concession with escrowed revenue share (ceiling 8% Lagos/Ogun, 15% agrarian/extractive states), per-module SaaS-equivalent yield, and transaction-fee legs on settlement.

**Legal prerequisites** (Zero-Capex Concession Structuring Gate): executed PPP Concession Agreement, gazetted statutory revenue-split order, approved NDPA Data Protection Compliance Statement. ENV-09 adds state environmental-protection agency enablement and carbon-market registry rules **[GAP — state-by-state instruments to be gazetted]**; CIT-11 adds NIMC integration agreement and civil-service biometric enrollment mandate **[GAP]**.

**Personas:** Governor/Executive Sponsor, SIRS Chairman, MDA revenue officers, GIS agency operators (NAGIS/TAGIS/LASGIS/BENGIS), environmental enforcement officers, civil-service payroll auditors, citizens (self-service), PPP concessionaire operations teams.

## 8. Roadmap, SLA & RTM

- **Roadmap:** five release trains (RT-01…RT-05) over a 24-month horizon; v3.0 modules slot into existing 4-wave rollout (`docs/delivery/rollout-matrix.md`) — citizen portal as a cross-state wave-2/3 enablement, environment module in Lagos/Ogun (industrial) and Taraba (forest surveillance) first.
- **SLA highlights:** deforestation alert response <4h from satellite detection to dispatched enforcement ticket; settlement webhook confirmation in real time; pen-test gate of zero cross-tenant leakage (REQ-SEC-01).
- **RTM:** requirement-to-repo traceability lives in `docs/delivery/rtm-v3.md`.
