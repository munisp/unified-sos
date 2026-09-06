# SOS API & Event Inventory — 20

Source: Jira Master Backlog Workbook · *API & Event Inventory* sheet (20 records).

> **Provenance note:** this sheet was ported verbatim (all rows and columns preserved). Its content derives from the workbook's generic SAFe/government template and is not Nigerian-SOS-program-specific; treat program-authoritative catalogs as the SOS-specific documents listed in [README.md](README.md).

| ID | Type | Name | Domain | Producer | Consumers | Protocol | Version | Rate Limit | SLA | Auth | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| API-01 | REST | identity.v1.users | Identity | IAM Service | Portals, Mobile | HTTPS/JSON | 1.2 | 1000 rps | 99.95% | OAuth 2.0 | Live |
| API-02 | REST | identity.v1.sessions | Identity | IAM Service | All apps | HTTPS/JSON | 1.1 | 2000 rps | 99.95% | OAuth 2.0 | Live |
| API-03 | REST | benefits.v1.applications | HHS | Enrollment Svc | Caseworker UI | HTTPS/JSON | 1 | 500 rps | 99.9% | mTLS + JWT | Beta |
| API-04 | REST | rules.v1.evaluate | HHS | Rules Engine | Enrollment, Provider | HTTPS/JSON | 1.3 | 1500 rps | 99.9% | mTLS | Live |
| API-05 | REST | tax.v1.efile | Revenue | Tax Portal | IRS MeF gateway | HTTPS/XML | 1 | 300 rps | 99.5% | Mutual TLS | In Dev |
| API-06 | REST | ui.v1.claims | Labor | UI Claims Svc | Claimant Portal | HTTPS/JSON | 1.1 | 800 rps | 99.9% | OAuth 2.0 | Live |
| API-07 | Event | ui.claim.filed | Labor | UI Claims Svc | Fraud ML, Analytics | Kafka Avro | 1 | 5k eps | 99.9% | SASL/SCRAM | Live |
| API-08 | Event | ui.claim.risk.scored | Labor | Fraud ML | UI Claims, SIEM | Kafka Avro | 1 | 5k eps | 99.9% | SASL/SCRAM | Beta |
| API-09 | REST | dmv.v1.registrations | Transport | DMV Svc | Portal, Voter Reg | HTTPS/JSON | 1 | 400 rps | 99.9% | OAuth 2.0 | In Dev |
| API-10 | REST | dmv.v1.mdl | Transport | DMV Svc | Verifier Apps | HTTPS/JSON | 0.9 | 200 rps | 99.5% | OAuth 2.0 | Alpha |
| API-11 | REST | court.v1.filings | Judiciary | Court CMS | Attorney Portal | HTTPS/JSON | 1 | 300 rps | 99.5% | OAuth 2.0 | In Dev |
| API-12 | Event | alert.dispatched | Public Safety | Alert Orchestrator | Analytics, Audit | Kafka JSON | 1 | 1k eps | 99.99% | SASL/SCRAM | Live |
| API-13 | REST | alert.v1.dispatch | Public Safety | Alert Orchestrator | EOC Console | HTTPS/JSON | 1.1 | 100 rps | 99.99% | mTLS + OAuth | Live |
| API-14 | Event | provider.enrollment.approved | HHS | Provider Svc | Analytics | Kafka Avro | 1 | 500 eps | 99.9% | SASL/SCRAM | In Dev |
| API-15 | REST | election.v1.results | Elections | Results Svc | Public Site | HTTPS/JSON | 1 | 2000 rps | 99.99% | API Key + WAF | Planned |
| API-16 | Event | voter.reg.submitted | Elections | Voter Reg | DMV, Analytics | Kafka Avro | 1 | 500 eps | 99.9% | SASL/SCRAM | Planned |
| API-17 | REST | siem.v1.events | Security | SIEM | SOC Console | HTTPS/JSON | 1 | 10k rps | 99.9% | mTLS | Live |
| API-18 | Event | soar.playbook.completed | Security | SOAR | Analytics | Kafka JSON | 1 | 200 eps | 99.9% | SASL/SCRAM | Beta |
| API-19 | REST | gis.v1.parcels | Public Records | GIS Svc | Parcel Viewer | HTTPS/JSON | 0.8 | 500 rps | 99.5% | API Key | Alpha |
| API-20 | Event | sis.enrollment.updated | Education | SIS Svc | Reporting | Kafka Avro | 1 | 1k eps | 99.9% | SASL/SCRAM | Planned |
