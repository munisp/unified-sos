# Backlog Workbook — Engineering Planning Catalogs

Docs-as-code mirror of the **Jira Master Backlog Workbook (XLSX, 10 sheets)** — the structured planning workbook for engineering teams referenced in the Master Deliverables Index.

## Imported Sheets (SOS program-specific)

| File | Source Sheet | Contents |
|---|---|---|
| [epic-catalog.md](epic-catalog.md) | Epic Catalog | 21 strategic epics across WP-01…WP-18 with tech stacks, release trains, pods, tenancy scope |
| [module-state-rollout-matrix.md](module-state-rollout-matrix.md) | State Rollout Matrix | MOD-01…MOD-17 module × 6-state deployment matrix with tiers, phases, anchor opportunities |
| [vendor-package-mapping.md](vendor-package-mapping.md) | Vendor Package Mapping | 9 commercial procurement lots mapping WPs to concession models and evaluation competencies |

## Imported Sheets (generic workbook template — ported verbatim, data-honesty note)

The remaining sheets carry **generic SAFe/government template content unrelated to the Nigerian SOS program** (US-centric references such as SNAP enrollment, DMV, IRS MeF e-filing, FedRAMP ATO, elections). They are now ported **verbatim** (all rows and columns preserved, including totals rows) for engineering-record completeness, each with a provenance note. They are **not** program-authoritative.

| File | Source Sheet | Contents |
|---|---|---|
| [feature-catalog.md](feature-catalog.md) | Feature Catalog | 35 features (FEAT-001…035) with parent epics, story points, priority, PI, team |
| [user-stories.md](user-stories.md) | User Stories | 38 stories (US-001…038) in canonical As-a/I-want/So-that form with acceptance criteria |
| [release-train-plan.md](release-train-plan.md) | Release Trains | 5 ARTs with PI windows, capacity/committed SP, RTE — see authoritative model below |
| [dependency-matrix.md](dependency-matrix.md) | Dependency Matrix | 12 cross-team dependencies with type, severity, owner, mitigation |
| [nfr-catalog.md](nfr-catalog.md) | NFR Catalog | 12 NFRs with targets, measurement method, verification, status |
| [api-event-inventory.md](api-event-inventory.md) | API & Event Inventory | 20 REST/event interfaces with protocol, version, rate limit, SLA, auth |
| [raid-log.md](raid-log.md) | RAID Log | 10 risks/assumptions/issues/dependencies with impact, probability, mitigation |

## Authoritative Program References (data-honesty note)

The authoritative release-train model for this program is [`../release-trains.md`](../release-trains.md) (RT-01…RT-05); the authoritative NFR/SLA set is [`../../procurement/sla-slo.md`](../../procurement/sla-slo.md); the authoritative API/event contracts live in [`contracts/`](../../../../contracts/README.md); the program risk register is [`../../governance/risk-register.md`](../../governance/risk-register.md).
