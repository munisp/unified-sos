# SOS Documentation Index

All platform documentation is organized to mirror the official SOS artifact suite (September 2026 editions): the Master Executive Pack, Architecture Blueprint, Implementation Backlog Pack, Procurement Pack, and Cross-State Rollout Matrix.

## Structure

| Directory | Scope | Source Artifact |
|---|---|---|
| [`business/`](business/sos-v3-business-specification.md) | v3.0 business specification port (vision, tenancy, 12-domain catalog, financial core, commercial/legal, roadmap/SLA) | SOS Business Specification Pack v3.0.0 |
| [`architecture/`](architecture/README.md) | System design goals, ADRs, bounded contexts, core deep-dives (ledger, geospatial, lakehouse, security, DR) | SOS Technical Specification |
| [`delivery/`](delivery/README.md) | Work Packages WP-01…WP-18, Release Trains RT-01…RT-05, 4-wave rollout matrix, 90-day playbook | Implementation Backlog Pack + Rollout Matrix |
| [`procurement/`](procurement/README.md) | 8-lot packaging, vendor qualification, 1,000-point QCBS scorecards, contract clauses, SLA/SLO & penalties, acceptance gates | Procurement & Vendor Delivery Framework |
| [`states/`](states/README.md) | Per-state profiles: baselines, modules, waves, legal dependencies, risks | Six-State Pipeline + Rollout Matrix |
| [`ppp-pipeline/`](ppp-pipeline/README.md) | The 30 ranked Technology-PPP opportunities (N1–N5, B1–B5, T1–T5, O1–O5, S1–S5, L1–L5) | State PPP Opportunities Report |
| [`governance/`](governance/README.md) | Compliance (ICRC/NDPA/NITDA), risk register, provenance framework, decision matrix | Master Executive Pack |

## Reading Order by Role

- **Governors / Executive Sponsors** → `states/` → `ppp-pipeline/` → `governance/decision-matrix.md`
- **Enterprise / Solution Architects** → `architecture/` → `contracts/` (repo root) → `delivery/work-packages.md`
- **PPP & Procurement Teams** → `procurement/` → `governance/compliance.md`
- **Delivery / PMO** → `delivery/` → `states/` → `tests/` (repo root)
- **Security / SOC** → `architecture/07-tenancy-security.md` → `SECURITY.md` (repo root)

## Provenance Convention

All documentation carries the mandatory data-honesty tags: **[LIVE]** verified fact · **[DERIVED]** analytical projection · **[GAP]** documented absence that is itself the opportunity.
