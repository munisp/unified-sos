# SOS NFR Catalog — 12

Source: Jira Master Backlog Workbook · *NFR Catalog* sheet (12 records).

> **Provenance note:** this sheet was ported verbatim (all rows and columns preserved). Its content derives from the workbook's generic SAFe/government template and is not Nigerian-SOS-program-specific; treat program-authoritative catalogs as the SOS-specific documents listed in [README.md](README.md).

| NFR ID | Category | Requirement | Target | Measurement | Applies To | Owner | Priority | Verification | Status |
|---|---|---|---|---|---|---|---|---|---|
| NFR-01 | Performance | API p95 latency | < 300 ms | APM synthetic | All services | J. Reeves | Critical | Load test | In Progress |
| NFR-02 | Performance | Batch job completion | < 4 hours | Job scheduler | Rules engine | M. Chen | High | Nightly run | Planned |
| NFR-03 | Availability | Citizen portal uptime | 99.95% | Status page | Citizen portals | J. Reeves | Critical | Monthly SLO report | In Progress |
| NFR-04 | Scalability | Concurrent users | 500k | Load test | UI, SNAP, Tax | R. Gomez | Critical | k6 tests | Planned |
| NFR-05 | Security | OWASP compliance | ASVS L2 | SAST/DAST | All apps | S. Ali | Critical | Pen test | In Progress |
| NFR-06 | Security | Data at rest encryption | AES-256 | KMS audit | All datastores | S. Ali | Critical | KMS report | In Progress |
| NFR-07 | Accessibility | WCAG conformance | WCAG 2.2 AA | axe-core | All citizen UIs | N. Alvarez | Critical | Manual audit | Planned |
| NFR-08 | Privacy | PII data retention | 7 years | Data catalog | HHS, Tax | P. Singh | High | Retention audit | Planned |
| NFR-09 | Compliance | FedRAMP posture | Moderate | 3PAO report | All cloud services | S. Ali | Critical | Annual ATO | In Progress |
| NFR-10 | Observability | MTTR | < 30 min | Incident tracker | All services | T. Brooks | High | Post-incident review | In Progress |
| NFR-11 | Localization | Language support | EN + ES + ZH | Language config | Citizen apps | N. Alvarez | High | Content review | Planned |
| NFR-12 | Interoperability | Standard APIs | REST + FHIR + NIEM | API registry | HHS, Justice | J. Reeves | High | API contract tests | Planned |
