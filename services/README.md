# SOS Services — Canonical Module Suite

One canonical codebase, configured per state via policy packs (80/20 rule). Each module directory holds the service source, its OpenAPI/AsyncAPI contract link, Helm values, and tests.

| Module | Domain | WP / Epic | Lot | Deploying States |
|---|---|---|---|---|
| [control-plane/](control-plane/) | Tenant provisioning & policy-pack engine | WP-01 / EPIC-01 | Lot 1 | All 6 |
| [mod-rev-core/](mod-rev-core/) | Revenue admin: STIN, assessment, billing, POS sync | WP-05 / EPIC-05 | Lot 3 | All 6 (common core) |
| [mod-gis-lands/](mod-gis-lands/) | Cadastre, e-C-of-O titling workflow | WP-06 / EPIC-06 | Lot 4 | All 6 |
| [mod-gis-luc/](mod-gis-luc/) | Land Use Charge valuation via satellite footprints | WP-06 / EPIC-06 | Lot 4 | Ogun, Benue, Lagos, Nasarawa |
| [mod-mining/](mod-mining/) | Mineral custody, weighbridge IoT, levies | WP-07 / EPIC-07 | Lot 5 | Nasarawa, Osun, Taraba, Ogun |
| [mod-forestry/](mod-forestry/) | Timber provenance, NDVI deforestation alerts | WP-07 / EPIC-08 | Lot 5 | Taraba, Ogun, Osun, Benue |
| [mod-transport-wim/](mod-transport-wim/) | Weigh-in-motion, ANPR, overload fines | WP-08 / EPIC-09 | Lot 6 | Ogun, Lagos, Nasarawa, Benue |
| [mod-mobility-switch/](mod-mobility-switch/) | Multimodal transit clearing (Cowry-compatible) | WP-08 / EPIC-10 | Lot 6 | Lagos, Ogun |
| [mod-agri-waybill/](mod-agri-waybill/) | Produce e-waybills, warehouse receipts | WP-09 / EPIC-11 | Lot 6/9 | Benue, Taraba, Nasarawa |
| [mod-health/](mod-health/) | Hospital billing, SHIA/NHIS claims | WP-10 / EPIC-12 | Lot 7 | Nasarawa, Osun, Benue, all 6 |
| [mod-education/](mod-education/) | Tertiary consolidated billing & bursary | WP-11 / EPIC-13 | Lot 7 | Osun, all 6 |
| [mod-market/](mod-market/) | Market stall cadastre, micro-tenancy billing | WP-12 / EPIC-14 | Lot 7 | Osun, Nasarawa, all 6 |
| [mod-police-cad/](mod-police-cad/) | 112 CAD dispatch, patrol geofencing | WP-13 / EPIC-15 | Lot 8 | All 6 (ratification-gated) |
| [mod-environment/](mod-environment/) | ENV-09: emissions telemetry compliance, effluent/timber permits, deforestation surveillance, carbon registry, EIA | v3.0 / ENV-09 | Lot 5 | Lagos, Ogun, Taraba (+ shared config Osun, Benue, Nasarawa) |
| [mod-citizen-portal/](mod-citizen-portal/) | CIT-11: citizen portal, Keycloak SSO wallet, e-petitions, payroll ghost-worker audit | v3.0 / CIT-11 | Lot 7 | All 6 |
| [mod-kyc-kyb/](mod-kyc-kyb/) | KYC-KYB: KYC/KYB case management, document AI (PaddleOCR/Docling/VLM), liveness anti-spoof, CAC/NIMC/tax/sanctions registry verification, review queues, hash-chained audit | v3.0 / KYC-KYB | Lot 1/7 | All 6 |
| [mod-geospatial/](mod-geospatial/) | GEO/LND: geospatial dataset registry, H3 indexing, processing jobs, GeoLibre self-hosted workbench projects, lakehouse publication seams | Stage 5 / GEO | Lot 4-6 | All 6 |
| [mod-geospatial-gateway/](mod-geospatial-gateway/) | GEO/LND: low-latency geospatial validation & job command gateway (Go) | Stage 5 / GEO | Lot 4-6 | All 6 |
| [mod-erp-bridge/](mod-erp-bridge/) | ERP-BRIDGE: tenant-isolated ERP/IFMIS journal bridge (Odoo XML-RPC, ERPNext REST, GIFMIS CSV/JSON export), settlement-event ingestion, COA mapping, hash-chained outbound log | v3.0 / ERP-BRIDGE | Lot 7 | All 6 |
| [lakehouse/](lakehouse/) | Delta Lake medallion, Flink streaming, Ray AI | WP-15 / EPIC-17/18 | Lot 7 | All 6 |

## Module Anatomy (standard layout)

```
services/<module>/
├── README.md           # this module's scope, contracts, config surface
├── cmd/ or src/        # service entrypoint (Go/Rust/Python per ADR-001)
├── internal/           # domain logic (no state-specific constants!)
├── openapi/ -> link    # contract lives in /contracts
├── deploy/             # Helm values overlay
└── tests/              # unit + contract tests (>85% coverage gate)
```

State-specific values (tax rates, fee heads, split formulas, SLA clocks) are **never** in code — they load from `config/states/<state>/` policy packs at runtime.
