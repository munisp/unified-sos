# SOS Cross-Team Dependencies — 12

Source: Jira Master Backlog Workbook · *Dependency Matrix* sheet (12 records).

> **Provenance note:** this sheet was ported verbatim (all rows and columns preserved). Its content derives from the workbook's generic SAFe/government template and is not Nigerian-SOS-program-specific; treat program-authoritative catalogs as the SOS-specific documents listed in [README.md](README.md).

| Dep ID | From (Item) | To (Item) | Type | Description | Needed By | Owner | Severity | Status | Mitigation |
|---|---|---|---|---|---|---|---|---|---|
| DEP-01 | FEAT-004 | FEAT-001 | Blocks | SNAP wizard needs SSO before pilot | 2025-03-15 | J. Reeves | High | Open | Fast-track SSO for pilot agency |
| DEP-02 | FEAT-009 | FEAT-001 | Blocks | UI intake requires citizen identity | 2025-03-01 | R. Gomez | High | In Progress | Interim IdP shim |
| DEP-03 | FEAT-005 | FEAT-004 | Blocks | Rules engine feeds SNAP wizard | 2025-03-30 | M. Chen | Critical | In Progress | Parallel dev + contract-first API |
| DEP-04 | FEAT-011 | FEAT-009 | Depends On | Fraud ML trained on intake data | 2025-05-01 | R. Gomez | High | Open | Synthetic data set for early training |
| DEP-05 | FEAT-018 | FEAT-034 | Depends On | Data fabric requires SIEM feeds | 2025-04-15 | T. Brooks | High | Open | Prioritize top 10 log sources |
| DEP-06 | FEAT-022 | FEAT-001 | Depends On | Alert dispatch uses SSO for admin | 2025-02-20 | J. Reeves | Medium | In Progress | Local admin fallback |
| DEP-07 | FEAT-013 | FEAT-001 | Depends On | DMV renewal uses SSO | 2025-05-01 | J. Reeves | Medium | Open | OAuth interim solution |
| DEP-08 | FEAT-031 | FEAT-013 | Depends On | Voter reg exchanges with DMV renewal | 2025-06-01 | A. Patel | High | Planned | Data exchange spec v1 |
| DEP-09 | FEAT-021 | FEAT-005 | Depends On | Provider screening reuses rules engine | 2025-05-15 | M. Chen | Medium | Planned | Shared rules library |
| DEP-10 | FEAT-015 | FEAT-003 | Depends On | E-filing needs IAL2 identity | 2025-06-15 | L. Wu | High | Open | Interim notary flow |
| DEP-11 | FEAT-032 | FEAT-034 | Depends On | Election reporting monitored by SIEM | 2025-06-30 | S. Ali | Critical | Planned | Dedicated SOC playbook |
| DEP-12 | FEAT-028 | FEAT-001 | Depends On | K-12 SIS uses federated identity | 2025-08-15 | J. Reeves | Medium | Planned | District IdP integrations |
