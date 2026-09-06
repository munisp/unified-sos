# Procurement Packaging & Lotting Strategy — Lots 1 to 8

To prevent single-vendor monopolization, mitigate systemic delivery failure, and ensure domain-specific excellence, SOS implementation is structured into **eight discrete procurement lots**, each with well-defined interface boundaries (REST/gRPC/Kafka/Dapr events) and independent SLA regimes.

| Lot | Title | Core Tech Stack | WP Mapping | Commercial Model |
|---|---|---|---|---|
| **Lot 1** | Core Platform, Control Plane & GitOps | Kubernetes, Dapr, APISIX, OpenAppSec, ArgoCD, Go/Rust | WP-01, WP-16 | Fixed SaaS / annual platform management fee |
| **Lot 2** | Identity, Access & Sovereign IAM | Keycloak, NIMC/CAC federation, OIDC/OAuth2, Vault | WP-02 | Per-active-citizen verification fee + annual base |
| **Lot 3** | Real-Time Ledger, Payments & Core Revenue | TigerBeetle, Mojaloop, Temporal, PostgreSQL, Go | WP-03, WP-04, WP-05 | Vendor-financed concession (% of collected IGR); transaction fee share 0.5–1.5% |
| **Lot 4** | Cadastral GIS, Land Titling & Geospatial Platform | Apache Sedona, PostGIS, GeoServer, Tegola, Delta Lake | WP-06, WP-14 | Concession fee — % of Land Use Charge & C-of-O fees (10–18%) |
| **Lot 5** | Natural Resources, Mining & Forestry Tracking | ThingsBoard IoT, mobile app, Fluvio, Sedona, Spark | WP-07, WP-09 | Concession fee — % of mineral haulage & timber tariffs (12–18%) |
| **Lot 6** | Transit, Corridor Haulage & Weigh-in-Motion | ANPR cameras, WIM sensors, Dapr, TigerBeetle, Flutter | WP-08 | Concession fee — % of road tariffs & haulage enforcement (15–20%) |
| **Lot 7** | Lakehouse Data Platform, Streaming & AI/ML | Delta Lake, Apache Flink, DataFusion, Ray, MinIO | WP-15 | Platform analytics SLA / gainshare on recovered leakage |
| **Lot 8** | Cyber Operations SOC, Wazuh XDR & Managed SRE | Wazuh, OpenCTI, OpenSearch, Kubecost, VictoriaMetrics | WP-17 | Annual managed services contract with availability SLAs |

## Anti-Monopoly Cross-Lot Restrictions

- No single bidding entity or consortium may be awarded **more than three (3) lots** in any single state.
- The vendor awarded **Lot 1** (Control Plane & Infrastructure) is **strictly disqualified** from Lot 3 (Payments & Revenue) and Lot 8 (SOC & SRE) — guaranteeing objective independent auditability and separation of operational duties.

## Commercial Guardrails

- **Concession tenor:** 5–7 years BOT, optional 3-year extension on performance KPIs and SEC approval.
- **Revenue-share ceilings:** ≤15% of net incremental IGR in agrarian/extractive states (Nasarawa, Benue, Taraba, Osun); ≤8% in high-volume industrial economies (Ogun, Lagos).
- **CapEx amortization cap:** once vendor CapEx + agreed IRR (capped at 22%) is fully amortized, revenue share steps down automatically to a baseline platform maintenance fee (≤3%).
- **Tapering:** step-down schedules (e.g., 15% on first ₦5bn, 10% on next ₦10bn, 6% on excess) score bonus points in evaluation.
