# SOS User Stories — 38 Stories

Source: Jira Master Backlog Workbook · *User Stories* sheet (38 records).

> **Provenance note:** this sheet was ported verbatim (all rows and columns preserved). Its content derives from the workbook's generic SAFe/government template and is not Nigerian-SOS-program-specific; treat program-authoritative catalogs as the SOS-specific documents listed in [README.md](README.md).

| Story ID | Parent Feature | As a... | I want to... | So that... | Acceptance Criteria | Points | Priority | Sprint | Assignee | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| US-001 | FEAT-001 | Citizen | log in with a single account across state services | I don't manage many passwords | SSO redirect works for 3 pilot agencies | 8 | Critical | S2025.01 | J. Reeves | In Progress |
| US-002 | FEAT-001 | Admin | manage IdP trust relationships | I can onboard agencies quickly | Admin UI CRUD; audit log | 5 | High | S2025.02 | J. Reeves | In Progress |
| US-003 | FEAT-002 | Citizen | enroll biometrics at a kiosk | I can access services faster | Kiosk captures + enrolls in <90s | 8 | High | S2025.03 | T. Brooks | Planned |
| US-004 | FEAT-003 | Citizen | verify identity remotely | I avoid an in-person visit | IAL2 flow completes with liveness | 13 | High | S2025.04 | J. Reeves | Planned |
| US-005 | FEAT-004 | Applicant | apply for SNAP online | I can get benefits faster | Wizard 8 steps; save/resume | 8 | Critical | S2025.02 | M. Chen | In Progress |
| US-006 | FEAT-004 | Applicant | apply in Spanish | I understand each question | Full Spanish localization | 5 | High | S2025.03 | M. Chen | In Progress |
| US-007 | FEAT-005 | Caseworker | see rule evaluation trace | I can explain determinations | Trace visible for every rule | 8 | Critical | S2025.03 | M. Chen | Planned |
| US-008 | FEAT-005 | Policy Analyst | version and publish rules | I can roll back safely | Version diff + rollback | 5 | High | S2025.04 | M. Chen | Planned |
| US-009 | FEAT-006 | Applicant | upload a photo of ID | I don't need to mail documents | OCR extracts 8+ fields | 5 | High | S2025.04 | M. Chen | Planned |
| US-010 | FEAT-007 | Taxpayer | e-file my state return | I get faster refunds | IRS MeF handoff success | 13 | High | S2025.05 | A. Patel | Planned |
| US-011 | FEAT-007 | Taxpayer | pay balance due via ACH | I don't mail checks | ACH confirmation email | 5 | High | S2025.06 | A. Patel | Planned |
| US-012 | FEAT-008 | Business Owner | upload sales tax CSV | I don't rekey data | CSV validation errors shown | 5 | Medium | S2025.07 | A. Patel | Planned |
| US-013 | FEAT-009 | Claimant | file a UI claim online | I get benefits quickly | Claim submitted in <20 min | 8 | Critical | S2025.02 | R. Gomez | In Progress |
| US-014 | FEAT-010 | Claimant | certify weekly via IVR | I can certify without internet | IVR flow < 4 min | 5 | Critical | S2025.03 | R. Gomez | In Progress |
| US-015 | FEAT-011 | Fraud Analyst | see risk score per claim | I prioritize investigations | Score + top features shown | 8 | Critical | S2025.04 | R. Gomez | Planned |
| US-016 | FEAT-012 | Citizen | request a birth certificate online | I don't visit the office | Cert mailed in 5 days | 5 | Medium | S2025.06 | K. Nguyen | Planned |
| US-017 | FEAT-013 | Vehicle Owner | renew registration online | I save time | Digital receipt emailed | 5 | High | S2025.05 | D. Foster | Planned |
| US-018 | FEAT-014 | Driver | present mDL from my phone | I don't carry a card | QR + BLE presentation works | 13 | High | S2025.07 | D. Foster | Planned |
| US-019 | FEAT-015 | Attorney | e-file case documents | I skip courthouse trips | Filing accepted + stamped | 8 | High | S2025.05 | L. Wu | Planned |
| US-020 | FEAT-016 | Judge | view my docket | I plan my day | Sorted docket loads <2s | 5 | High | S2025.06 | L. Wu | Planned |
| US-021 | FEAT-017 | Hotline Worker | triage abuse reports | priority cases surface first | Priority queue with SLA | 8 | High | S2025.07 | S. Ali | Planned |
| US-022 | FEAT-018 | Analyst | query cross-agency events | I find suspects faster | Search returns <3s | 8 | Critical | S2025.05 | T. Brooks | Planned |
| US-023 | FEAT-019 | Officer | search across states | I locate persons of interest | Federated search UI | 5 | High | S2025.06 | T. Brooks | Planned |
| US-024 | FEAT-020 | Vendor | register self-service | I bid on contracts quickly | Onboarding < 2 days | 5 | Medium | S2025.06 | P. Singh | Planned |
| US-025 | FEAT-021 | Provider | apply as Medicaid provider | I can serve patients | Screening auto-checks pass | 8 | High | S2025.05 | M. Chen | Planned |
| US-026 | FEAT-022 | EOC Officer | dispatch an alert to a county | citizens are warned quickly | Alert reaches devices <60s | 8 | Critical | S2025.02 | T. Brooks | In Progress |
| US-027 | FEAT-023 | Nurse | renew RN license | I keep practicing | Renewal completes online | 5 | Medium | S2025.07 | K. Nguyen | Planned |
| US-028 | FEAT-024 | Resident | report a pothole | it gets fixed | 311 case auto-routed to DOT | 5 | Medium | S2025.05 | J. Reeves | Planned |
| US-029 | FEAT-025 | Resident | attach a photo from mobile | the crew has context | Photo uploads + geotags | 3 | Medium | S2025.06 | J. Reeves | Planned |
| US-030 | FEAT-026 | Surveyor | view parcel boundaries | I do accurate surveys | Layers render < 3s | 8 | Low | S2026.01 | D. Foster | Backlog |
| US-031 | FEAT-027 | Corrections Officer | record inmate movement | the head count stays accurate | Movement audit trail | 5 | High | S2025.07 | T. Brooks | Planned |
| US-032 | FEAT-028 | Registrar | submit enrollment nightly | state data stays current | Nightly job SLO met | 8 | High | S2025.07 | L. Wu | Planned |
| US-033 | FEAT-030 | Facility Operator | apply for air permit | I stay compliant | SLA countdown visible | 5 | Medium | S2026.01 | P. Singh | Backlog |
| US-034 | FEAT-031 | Voter | register online | I can vote | DMV data confirms identity | 8 | Critical | S2025.05 | A. Patel | Planned |
| US-035 | FEAT-032 | Election Official | publish results | public sees timely results | Results pipeline auditable | 8 | Critical | S2025.06 | A. Patel | Planned |
| US-036 | FEAT-033 | Job Seeker | find matched jobs | I get hired | Matches ranked by fit | 5 | Medium | S2025.07 | R. Gomez | Planned |
| US-037 | FEAT-034 | SOC Analyst | onboard a log source | I detect threats | Source ingested + validated | 5 | Critical | S2025.03 | S. Ali | In Progress |
| US-038 | FEAT-035 | SOC Analyst | run a phishing playbook | I contain incidents fast | Playbook completes < 5 min | 8 | Critical | S2025.04 | S. Ali | Planned |
