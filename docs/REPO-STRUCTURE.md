# Repository Structure — Recommendation Record

**Status:** Adopted · **Date:** 2026-09-06 · **Scope:** `munisp/unified-sos`

This document records the recommended GitHub repository structure for the State Operating System (SOS) platform and the rationale for each decision, mapped to the official SOS artifact suite.

## Design Principles

1. **Canonical-core monorepo.** The architecture blueprint mandates one identical core codebase across all states with state variation injected via policy packs. A single monorepo with a `services/` per-module layout + `config/states/` policy packs enforces this physically.
2. **Docs-as-code mirror of the artifact suite.** `docs/` reproduces the six official deliverables (Executive Pack, Architecture Blueprint, Backlog Pack, Procurement Pack, Rollout Matrix, PPP Pipeline) as versioned markdown, so contracts, ADRs and procurement baselines drift-guard with the code.
3. **Contracts-first.** OpenAPI 3.1 / AsyncAPI / policy-pack JSON schemas live in a top-level `contracts/` directory — satisfying Clause 19.4 (anti-lock-in, OpenAPI 3.1 obligation).
4. **Money correctness is isolated.** `ledger/` holds the TigerBeetle chart-of-accounts and atomic split reference code (ADR-002), with CODEOWNERS requiring dual review.
5. **Tenancy tiers are infrastructure code.** `infra/` carries per-tier (Dedicated / Hybrid / Shared) Kubernetes, Helm, Terraform and ArgoCD layouts matching the tenancy sizing model.
6. **Procurement gates are executable.** `tests/` maps FAT/SAT/stress/pen-test acceptance criteria to runnable specs, so the 1,000-point QCBS scorecard and milestone gates are verifiable in CI.

## Top-Level Layout

```
unified-sos/
├── README.md                  # Platform overview, repo map, roadmap
├── LICENSE                    # Apache-2.0 (open-source whitelist compliance)
├── CONTRIBUTING.md            # 80/20 rule, provenance tags, SLAs, workflow
├── SECURITY.md                # Vulnerability reporting & CVSS remediation SLAs
├── CODEOWNERS                 # Pod-based review routing (Pods 1–5 + state pods)
├── .gitignore                 # Secret/data exclusion (NDPA-aware)
├── docs/
│   ├── REPO-STRUCTURE.md      # This record
│   ├── architecture/          # Blueprint: goals, ADR-001..007, deep-dives
│   ├── delivery/              # WP-01..18, RT-01..05, rollout matrix, 90-day playbook
│   ├── procurement/           # 8 lots, QCBS scorecards, clauses, SLA/SLO, acceptance
│   ├── states/                # 6 state profiles (Lagos, Ogun, Osun, Benue, Nasarawa, Taraba)
│   ├── ppp-pipeline/          # 30 ranked opportunities + module×state fit matrix
│   └── governance/            # ICRC/NDPA/NITDA compliance, risk register, decisions
├── services/                  # Core microservice modules + control plane
│   ├── control-plane/         # WP-01: tenant provisioning engine (sosctl backend)
│   ├── mod-rev-core/          # WP-05: STIN, assessment engine, billing
│   ├── mod-gis-lands/         # WP-06: cadastre, e-C-of-O workflow
│   ├── mod-gis-luc/           # WP-06: Land Use Charge valuation
│   ├── mod-mining/            # WP-07: mineral custody & royalties
│   ├── mod-forestry/          # WP-07: timber provenance & NDVI alerts
│   ├── mod-transport-wim/     # WP-08: weigh-in-motion & ANPR corridors
│   ├── mod-agri-waybill/      # WP-09: produce e-waybill & warehouse receipts
│   ├── mod-health/            # WP-10: hospital billing & SHIA claims
│   ├── mod-education/         # WP-11: tertiary consolidated billing
│   ├── mod-market/            # WP-12: market stall cadastre & micro-tenancy
│   ├── mod-police-cad/        # WP-13: CAD / 112 dispatch (ratification-gated)
│   ├── mod-mobility-switch/   # Multimodal transit clearing (Lagos/Ogun)
│   └── lakehouse/             # WP-15: Delta Lake / Flink / Ray pipelines
├── contracts/
│   ├── openapi/               # OpenAPI 3.1 REST contracts
│   ├── asyncapi/              # Event contracts (ng.sos.* topics)
│   └── policy-packs/          # JSON Schema for state policy packs
├── db/
│   └── migrations/            # PostGIS cadastre DDL + RLS tenancy policies
├── ledger/
│   ├── chart-of-accounts.md   # 128-bit account layout & codes
│   └── splits/                # Atomic multi-leg statutory split reference (Go)
├── geospatial/
│   └── sedona/                # Distributed spatial join & NDVI jobs
├── infra/
│   ├── k8s/                   # Cluster bases per tenancy tier
│   ├── helm/                  # SOS platform charts
│   ├── terraform/             # Sovereign cloud provisioning
│   └── gitops/                # ArgoCD tenant declarations
├── config/
│   └── states/                # lagos/ ogun/ osun/ benue/ nasarawa/ taraba/ policy packs
├── edge/                      # Offline-first POS & checkpoint edge daemon design
├── tests/                     # k6/Locust load specs, FAT/SAT acceptance mapping
├── tools/
│   └── sosctl/                # Tenant provisioning CLI
└── .github/
    ├── workflows/             # ci.yml, security-scan.yml, sbom.yml
    ├── ISSUE_TEMPLATE/        # bug, module feature, state onboarding
    └── PULL_REQUEST_TEMPLATE.md
```

## Mapping to Work Packages & Lots

| Repo Area | Work Packages | Procurement Lots |
|---|---|---|
| `services/control-plane/`, `infra/`, `tools/` | WP-01, WP-16 | Lot 1 |
| `services/mod-rev-core/`, `ledger/`, payment adapters | WP-03, WP-04, WP-05 | Lot 3 |
| `services/mod-gis-*`, `db/`, `geospatial/` | WP-06, WP-14 | Lot 4 |
| `services/mod-mining/`, `mod-forestry/` | WP-07, WP-09 | Lot 5 |
| `services/mod-transport-wim/`, `edge/` | WP-08 | Lot 6 |
| `services/lakehouse/` | WP-15 | Lot 7 |
| `.github/workflows/security-scan.yml`, SOC configs | WP-17 | Lot 8 |
| Keycloak realm configs under `infra/` | WP-02 | Lot 2 |

## Alternatives Considered

- **Polyrepo per module** — rejected: breaks the canonical-core guarantee, multiplies CI/SBOM overhead, and makes cross-module contract drift likely.
- **Docs in a separate repo** — rejected: ADRs, contracts and procurement baselines must version-lock with code for escrow and audit (Clause 16.3).
- **Per-state repos** — rejected: state variation is configuration, not code (80/20 rule); per-state repos would invite bespoke forks and vendor lock-in by the back door.

## Conventions

- Provenance tags `[LIVE]` / `[DERIVED]` / `[GAP]` are mandatory on material figures.
- All API changes require contract updates in `contracts/` in the same PR.
- Policy-pack changes require review per CODEOWNERS and a compliance note for NDPA-sensitive fields.
