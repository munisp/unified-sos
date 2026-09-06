# Backlog Workbook — Engineering Planning Catalogs

Docs-as-code mirror of the **Jira Master Backlog Workbook (XLSX, 10 sheets)** — the structured planning workbook for engineering teams referenced in the Master Deliverables Index.

## Imported Sheets (SOS program-specific)

| File | Source Sheet | Contents |
|---|---|---|
| [epic-catalog.md](epic-catalog.md) | Epic Catalog | 21 strategic epics across WP-01…WP-18 with tech stacks, release trains, pods, tenancy scope |
| [module-state-rollout-matrix.md](module-state-rollout-matrix.md) | State Rollout Matrix | MOD-01…MOD-17 module × 6-state deployment matrix with tiers, phases, anchor opportunities |
| [vendor-package-mapping.md](vendor-package-mapping.md) | Vendor Package Mapping | 9 commercial procurement lots mapping WPs to concession models and evaluation competencies |

## Excluded Sheets (data-honesty note)

The source workbook also contained sheets carrying **generic template filler unrelated to the Nigerian SOS program** (US-centric references such as SNAP enrollment, DMV, IRS MeF e-filing, FedRAMP ATO, elections). Consistent with the program's data-honesty and provenance framework, these sheets were **deliberately not imported**: Feature Catalog, Release Trains (ART capacity sheet), Dependency Matrix, NFR Catalog, API & Event Inventory, and RAID Log.

The authoritative release-train model for this program is [`../release-trains.md`](../release-trains.md) (RT-01…RT-05); the authoritative NFR/SLA set is [`../../procurement/sla-slo.md`](../../procurement/sla-slo.md); the authoritative API/event contracts live in [`contracts/`](../../../../contracts/README.md); the program risk register is [`../../governance/risk-register.md`](../../governance/risk-register.md).

If corrected Nigerian-program versions of those sheets are produced, import them here following the same conventions.
