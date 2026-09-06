# Boardroom Decision Matrix & Master Deliverables Index

## Decisions Required from State Leadership (Day-1 Execution)

1. **Concession Approval** — formally approve the Zero-CapEx Turnkey Technology PPP Concession Agreement under the State PPP Law / ICRC guidelines.
2. **Statutory Split Order** — authorize the Ministry of Finance to gazette the automated TigerBeetle revenue-split schedule (CRF, MDA retention, PPP concessionaire).
3. **Inter-Agency Mandate** — Executive Order mandating all MDAs to federate data registries into the SOS.
4. **Project Steering Team** — appoint the State PMO Lead for the immediate 90-day Wave-0 deployment.

## Master Deliverables Index & Traceability

| Artifact | Scope | Location |
|---|---|---|
| Technical Solution Architecture | OpenAPI contracts, PostGIS DDLs, TigerBeetle code, ADRs | `docs/architecture/`, `contracts/`, `db/`, `ledger/` |
| Implementation Backlog Pack | 18 Work Packages (WP-01…WP-18), epics, user stories | `docs/delivery/work-packages.md` |
| Procurement & Vendor Framework | 8 RFP lots, evaluation scorecards, SoWs, SLA clauses | `docs/procurement/` |
| Cross-State Rollout Matrix | 5-pillar readiness, 24-month roadmap, 30 assets | `docs/delivery/rollout-matrix.md`, `docs/states/` |
| PPP Opportunity Pipeline | 30 ranked state opportunities, module×state fit | `docs/ppp-pipeline/` |
| Geospatial Analytics | Sedona/PostGIS/Ray benchmarks and jobs | `docs/architecture/04-geospatial-engine.md`, `geospatial/` |

## Value Proposition vs Traditional Point-Solution Model

| Strategic Dimension | Traditional Model | SOS Model |
|---|---|---|
| Capital expenditure | High upfront state CapEx, recurring budget drain | **Zero upfront CapEx** — 100% turnkey vendor-financed PPP |
| Revenue settlement | Manual batch reconciliation; delayed TSA remittance | Real-time split: 120-bit TigerBeetle double-entry atomic split |
| Geospatial intelligence | Static shapefiles, disconnected paper cadastres | Apache Sedona: planetary-scale spatial AI & remote sensing |
| Institutional sovereignty | Vendor lock-in, opaque proprietary databases | Full state ownership: permissive FOSS & sovereign data residency |
