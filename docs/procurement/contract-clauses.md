# Mandatory Contractual Clauses & Governance Framework

Non-negotiable legal clauses incorporated into every Concession Agreement and Vendor Contract under the SOS program.

## Clause 14.1 — Sovereign Data Ownership & NDPA 2023 Localization

> "The State shall at all times remain the sole, absolute, and exclusive owner of all data, records, spatial geometries, transaction histories, citizen identities, and revenue intelligence generated, ingested, or processed by the State Operating System. The Vendor / Concessionaire acts solely as a Data Processor under the Nigeria Data Protection Act (NDPA) 2023. Under no circumstances shall any State data be transmitted, stored, replicated, or backed up to any data center or cloud facility located outside the sovereign territorial boundaries of the Federal Republic of Nigeria without express written authorization from the State Attorney General and the National Data Protection Commission."

## Clause 16.3 — Open-Source Licensing & Intellectual Property Escrow

> "All bespoke software modules, integrations, API connectors, and configuration scripts developed under this Contract shall be licensed to the State under the Apache License, Version 2.0 or MIT License. For any proprietary background technology utilized, the Vendor shall deposit complete source code, compilation scripts, build artifacts, container images, and technical documentation into a tripartite Sovereign Software Escrow with a certified Nigerian Escrow Agent. Escrow release triggers shall include Vendor bankruptcy, material breach of SLA for sixty (60) consecutive days, or failure to support the platform."

## Clause 19.4 — Anti-Vendor Lock-In & Open API Obligations

> "The Vendor guarantees that all data stored within the SOS platform can be extracted at any time by the State in open, non-proprietary formats (PostgreSQL SQL dumps, GeoParquet, Delta Lake Parquet, GeoJSON, CSV) without vendor intervention or specialized fee. All platform microservices must expose documented, RESTful or gRPC APIs complying with OpenAPI 3.1 specifications. The Vendor shall not impose proprietary encryption or obfuscation on database tables or message bus topics."

## Clause 22.2 — Programmatic Statutory Revenue Settlement

> "All revenues mobilized through the platform must clear directly into the State Consolidated Revenue Fund (CRF) or gazetted TSA holding accounts. The Vendor's contracted concessionaire revenue share shall be computed and distributed strictly through automated TigerBeetle ledger execution at the end of each clearing cycle. Under no circumstances shall the Vendor collect, hold, or escrow gross State revenues into any private bank account prior to State statutory deduction."

## Real-Time Dual Auditability

- **Auditor-General read-only replicas:** dedicated real-time read-only replicas of the TigerBeetle ledger and Delta Lake storage with cryptographic proof verification.
- **Accountant-General TSA verification:** real-time visibility into all bank clearing settlements, verifying gross funds credit the State TSA instantly upon collection.

## Program Governance RACI

| Activity | State PPP Unit/BPP | State CIO/CDO | State IRS/Finance | Lead Vendor | Auditor-General |
|---|---|---|---|---|---|
| RFP issuance & tender evaluation | **A** | **R** | C | I | I |
| Control plane & architecture sign-off | C | **A** | I | **R** | I |
| Ledger & revenue settlement validation | C | **R** | **A** | **R** | C |
| Security, NDPA & cyber operations | I | **A** | I | **R** | C |
| Field hardware & corridor deployment | I | **R** | **A** | **R** | I |
| Monthly revenue-share reconciliation | I | I | **A** | **R** | **R** |
| Final exit handover & code transfer | **A** | **R** | **R** | **R** | **A** |

A = Accountable · R = Responsible · C = Consulted · I = Informed
