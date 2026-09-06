# Verification, Testing & Multi-Stage Acceptance Framework

## Five-Stage Acceptance Testing Lifecycle

No milestone payment or revenue-sharing commencement occurs without formal sign-off by the State Technical Steering Committee across all five stages.

| Stage | Scope | Key Metrics |
|---|---|---|
| **Stage 1: Factory Acceptance Testing (FAT)** | Vendor staging environment | Microservice unit coverage >85%; OpenAPI schema validation; Dapr component bindings; container vulnerability scanning |
| **Stage 2: Performance & Stress Testing** | Simulated high concurrency (Locust / k6) | Sustained 500,000 TPS TigerBeetle; 50,000 concurrent API req/s APISIX; 10,000 concurrent Sedona spatial joins without error |
| **Stage 3: Security & Penetration Testing** | Independent CREST-certified firm | Zero critical/high vulnerabilities; full OWASP Top 10 mitigation; flawless Wazuh XDR SIEM alerting |
| **Stage 4: Site Acceptance Testing (SAT)** | Live state infrastructure | Hardware integration (weighbridges, ANPR, POS, biometric scanners); live bank payment clearing settlement |
| **Stage 5: UAT & Go-Live Gates** | Production cutover | See gates below |

Test specs implementing these stages live in [`tests/`](../../../tests/README.md).

## 24-Month Build-Operate-Transfer Handover

| Phase | Ownership Model | Activities | State Civil-Service Capability Target |
|---|---|---|---|
| Phase 1 (M1–6) | 100% vendor-led (state shadowing) | Core platform, control plane, TigerBeetle ledger, payment switch, initial tax modules | 50 state IT personnel embedded in architecture, DevOps, DB operations |
| Phase 2 (M7–12) | 70% vendor / 30% state | Cadastral GIS, WIM corridors, mining custody, field POS expansion | State engineers run L1/L2 helpdesk and daily ledger reconciliations |
| Phase 3 (M13–18) | 30% vendor / 70% state | Lakehouse platform, Ray AI valuation, education & health billing | State DevOps manages K8s deployments, GitOps pipelines, Keycloak realms |
| Phase 4 (M19–24) | 100% state autonomous | Autonomous operation; vendor as L3 support + upstream OSS maintainer | Full operational autonomy, zero vendor dependency |

## SBOM & Open-Source Governance

- Every deliverable ships an automated, cryptographically signed **SBOM** (CycloneDX v1.5 / SPDX v2.3).
- License whitelist: MIT, Apache-2.0, BSD 2/3-Clause, ISC, PostgreSQL. Copyleft requires written State Attorney-General approval.
- Enforced in CI via `.github/workflows/sbom.yml` and `security-scan.yml`.
