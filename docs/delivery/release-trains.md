# Release Trains, Engineering Pods & Environment Readiness

## The Five Release Trains (24-Month Horizon)

| Train | Focus | Work Packages | Window | Key Exit Milestone |
|---|---|---|---|---|
| **RT-01: Sovereign Foundation & Control Plane** | Global multi-tenant control plane, K8s infra, Keycloak IAM, APISIX edge security, DevSecOps | WP-01, WP-02, WP-16, WP-17 | M1–M3 | Multi-tenant K8s active; state realms configured; zero-trust edge operational |
| **RT-02: Financial Ledger & Core Revenue** | TigerBeetle kernel, Mojaloop switch, STIN assessment engine, POS offline sync | WP-03, WP-04, WP-05, WP-18 | M3–M7 | First ₦1bn settled; 3-way splits verified; POS edge sync live |
| **RT-03: Spatial Cadastre & Natural Resources** | PostGIS + Sedona dual engine, C-of-O titling, LUC, mining & forestry RFID | WP-06, WP-07, WP-14 | M6–M12 | 500k+ parcels indexed; automated LUC billing; mineral weighbridges integrated |
| **RT-04: Sector-Specific Economic Modules** | Agro-hubs & e-waybill, inter-state WIM, hospital EHR billing, education bursary, markets | WP-08…WP-12 | M10–M18 | Corridor WIM automated; hospital fee leakage eliminated; commodity exchange live |
| **RT-05: Lakehouse AI & Advanced Security** | Delta Lake medallion, Flink streaming, DataFusion Gold, Ray AI valuation, Police CAD | WP-13, WP-15 | M16–M24 | Sub-second governor analytics; Ray AI valuation; Police CAD federation |

## Engineering Organization — Pod Topology

| Pod | Focus & WP Ownership | Staffing | Key Artifacts |
|---|---|---|---|
| **Pod 1: Core Financial & Settlement** | TigerBeetle kernel, Mojaloop, NIBSS (WP-03/04) | 1× Principal Systems Eng (Go/Rust), 2× Backend, 1× Payment QA | Ledger kernel, split engine, bank adapters |
| **Pod 2: Geospatial & Land Systems** | Sedona, PostGIS, Martin, C-of-O (WP-06/14) | 1× Lead GIS Architect, 2× Spatial Data Eng (Python/Sedona), 1× Frontend GIS | Cadastral viewer, footprint matcher, titling workflows |
| **Pod 3: Revenue & Sector Modules** | Tax core, Mining/Forestry IoT, WIM, Health (WP-05, 07–12) | 1× Lead Product Eng, 4× Full-Stack (Go/Vue), 2× Mobile/Edge | Taxpayer portal, offline POS agent, telematics connector |
| **Pod 4: Lakehouse & Data AI** | Delta Lake, Flink, DataFusion, Ray (WP-15) | 1× Principal Data Architect, 2× Data Eng, 1× ML Eng (Ray) | Executive KPI dashboard, anomaly detector, property AVM |
| **Pod 5: Platform, SecOps & SRE** | K8s, APISIX, Keycloak, Wazuh, Kubecost, CI/CD (WP-01/02/16/17) | 1× Lead SRE, 2× DevSecOps, 1× SOC Analyst | Zero-trust clusters, GitOps pipelines, SOC monitoring |
| **State Delivery Pods (×6)** | On-site onboarding, legacy data cleansing, MDA training, POS deployment | 1× State Engagement Director, 2× Solution Architects, 4× Field Support per state | State policy packs, training, go-live certification |

## Environment Readiness Checklist (pre-commissioning gate)

| Layer | Requirement | Verification | SLA/Standard |
|---|---|---|---|
| Sovereign Compute & K8s | K8s v1.28+ bare-metal/sovereign cloud; ≥6 workers (64 vCPU/256 GB) Tier-2; 16 nodes Tier-1 | `kubectl get nodes` + Kubecost probe | Zero control-plane eviction; NVMe pools active |
| Network & Edge DNS | State apex domain with DNSSEC (`*.nasarawa.gov.ng`); TLS 1.3 wildcards | SSL Labs A+ | HTTPS/TLS 1.3 forced; HTTP/2 + gRPC |
| Database Persistence | PostgreSQL 16+ / PostGIS 3.4+ with Patroni sync replication; TigerBeetle 6-replica on NVMe | TigerBeetle benchmark + `pg_isready` | Sub-5 ms writes; failover < 15 s |
| Perimeter Security & WAF | APISIX + OpenAppSec in block mode | OWASP Top 10 automated pentest | 100% SQLi/tamper block rate |
| Identity Federation | Keycloak multi-realm linked to NIMC NIN + CAC registry APIs | NIMC sandbox synthetic call | Sub-800 ms identity resolution |

## Turnkey Milestone Gates (vendor payout linkage)

| Milestone | WPs | Verification Metric | Commercial Gate |
|---|---|---|---|
| M1: Platform Ingress & IAM | WP-01, 02, 16, 17 | SOC pen-test sign-off; 500 state officers logged in | 20% of mobilization fee |
| M2: Financial Kernel & Revenue Go-Live | WP-03, 04, 05 | First ₦100m collected and auto-split to CRF + escrow | 30% of setup fee + concession share initiated |
| M3: Geospatial Cadastre & Asset Tracking | WP-06, 07, 14 | C-of-O < 14 days; first 5,000 automated LUC bills | 30% of setup fee |
| M4: Full Lakehouse AI & Commissioning | WP-08–13, 15, 18 | Sub-second dashboards; complete handover to State DTO | 20% final retainage |
