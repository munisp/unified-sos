# Vendor Package & Procurement Mapping (9 Commercial Lots)

Source: Jira Master Backlog Workbook · *Vendor Package Mapping* sheet. Commercial packaging of work packages into nine procurement lots for PPP concessionaires, system integrators, and OEMs.

> **Relationship to the Procurement Pack:** this 9-lot engineering packaging is the workbook variant of the **8-lot strategic framework** in [`../../procurement/lots.md`](../../procurement/lots.md). Mapping: LOT-01 ≙ Lots 1+8 (platform & SIEM), LOT-02 ≙ Lot 3, LOT-03 ≙ Lot 4 (+ WP-18), LOT-04 ≙ Lot 5, LOT-05 ≙ Lot 6, LOT-06 ≙ Lot 7-agri split, LOT-07 ≙ social modules, LOT-08 ≙ public safety, LOT-09 ≙ Lot 7 (lakehouse/AI). The Procurement Pack's anti-monopoly cross-lot restrictions apply to both packagings.

| Lot | Package | Domain | WPs | Commercial Model | Term | Key Vendor Deliverables | Evaluation Competencies | Target Share |
|---|---|---|---|---|---|---|---|---|
| LOT-01 | Core Platform & Cyber SIEM | Platform & Security | WP-01, WP-02, WP-16, WP-17 | BTO / Turnkey SaaS | 5 yrs | K8s infrastructure, Keycloak IAM, Istio mTLS, Wazuh SIEM, OpenAppSec ML-WAF | CKA/CKS, ISO 27001, DevSecOps, 99.999% SLA record | 5–8% platform fee |
| LOT-02 | Financial Ledger & Payment Switch | Core Financials | WP-03, WP-04, WP-05 | Vendor Concession | 7 yrs | TigerBeetle ledger, Mojaloop ISO 20022 hub, NIBSS/Remita adapters | Payment switch licensing, high-throughput distributed systems, ISO 20022 | 8–15% transaction fee |
| LOT-03 | Cadastral GIS & Land Titling | Land Administration | WP-06, WP-14, WP-18 | Vendor Concession (BTO) | 10 yrs | NAGIS/BENGIS/LASGIS modernization, automated C-of-O digital signing, LUC rollouts | Cadastral surveying licenses, PostGIS/Sedona, large-scale GIS rollout | 10–18% LUC / C-of-O share |
| LOT-04 | Extractive Resources & Minerals | Solid Minerals & Forestry | WP-07, WP-14 | Vendor Concession | 7 yrs | Weighbridge IoT telemetry, lithium/gold haulage manifests, forestry tagging | Mining IoT hardware, industrial telemetry, remote edge computing | 12–18% extractive levies |
| LOT-05 | Inter-State Transit & WIM Corridors | Transport & Logistics | WP-08 | Vendor Concession (BOT) | 10 yrs | WIM sensor civil works, ANPR cameras, automated toll billing | Highway civil engineering, high-speed ANPR, sensor calibration, union relations | 15–22% overload penalties |
| LOT-06 | Agro-Supply Chain & Warehousing | Agriculture | WP-09 | PPP Joint Venture | 7 yrs | Border e-waybills, warehouse receipt registry | Agri-tech supply chain, grain grading, warehouse collateralization | 8–12% hub transit fees |
| LOT-07 | Social Infrastructure (Health/Edu/Markets) | Healthcare & Education | WP-10, WP-11, WP-12 | SaaS + Performance Fee | 5 yrs | Hospital billing, SHIS claims switch, university bursary automation, market stall titling | HIS systems, university ERP integration, POS retail operations | 6–10% social revenue share |
| LOT-08 | Safe City & Police Dispatch CAD | Public Safety & Defense | WP-13, WP-17 | Government CapEx/Opex | 5 yrs | 112 CAD dispatch, state-police GIS incident map, CCTV metadata ingestion, secure comms | Public-safety comms, 112 CAD systems, secure radio/LTE | Fixed service-level fee |
| LOT-09 | Lakehouse Data Platform & AI | Data & AI | WP-14, WP-15 | Turnkey Implementation | 5 yrs | Delta Lake medallion, Flink CDC, Ray AI valuation, governor dashboards | Data platform engineering, Delta/Spark scale, distributed ML, executive BI | Fixed milestone + support |

All lots carry the mandatory clauses (14.1 data sovereignty, 16.3 escrow, 19.4 anti-lock-in, 22.2 statutory settlement) and SLA/SLO penalties from [`../../procurement/`](../../procurement/README.md).
