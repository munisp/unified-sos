# Module-to-State Rollout Matrix (MOD-01 … MOD-17)

Source: Jira Master Backlog Workbook · *State Rollout Matrix* sheet. Deployment roadmap mapping each SOS functional module across the six pilot states.

| Module ID | Module | Domain | Nasarawa | Benue | Taraba | Ogun | Osun | Lagos | Deployment Pattern | Primary Anchor Opportunity |
|---|---|---|---|---|---|---|---|---|---|---|
| MOD-01 | mod-control-plane | Platform Core | Tier-2 (P1) | Tier-2 (P1) | Tier-2 (P1) | Tier-2 (P1) | Tier-2 (P1) | Tier-1 (P1) | Shared Cloud / Dedicated VPC | Central platform multi-tenancy |
| MOD-02 | mod-iam | Identity & Access | Tier-2 (P1) | Tier-2 (P1) | Tier-2 (P1) | Tier-2 (P1) | Tier-2 (P1) | Tier-1 (P1) | Realm-per-State Keycloak | Sovereign citizen/civil-service ID |
| MOD-03 | mod-ledger | Financial Core | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-1 (P2) | TigerBeetle Cluster (VSR) | Real-time revenue split & TSA |
| MOD-04 | mod-payments | Payment Switch | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-1 (P2) | Mojaloop Interoperable Hub | Multi-bank settlement & POS sync |
| MOD-05 | mod-rev-core | Core Revenue | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-2 (P2) | Tier-1 (P2) | PostgreSQL RLS / Temporal | PAYE, withholding tax, stamp duty |
| MOD-06 | mod-gis-lands | Land Admin | Tier-2 (P3) | Tier-2 (P3) | Tier-2 (P3) | Tier-2 (P3) | Tier-2 (P3) | Tier-1 (P3) | PostGIS / GeoServer / C-of-O | NAGIS, BENGIS, TAGIS, LASGIS |
| MOD-07 | mod-gis-luc | Property Tax | Tier-2 (P3) | Tier-2 (P3) | Tier-2 (P3) | Tier-2 (P3) | Tier-2 (P3) | Tier-1 (P3) | Sedona / Ray Valuation | Land Use Charge & ground rent |
| MOD-08 | mod-mining | Solid Minerals | Tier-2 (P3) | N/A | Tier-2 (P3) | Tier-2 (P4) | Tier-2 (P3) | N/A | IoT Weighbridge / QR Transit | Nasarawa lithium, Osun gold |
| MOD-09 | mod-forestry | Natural Resources | N/A | Tier-2 (P4) | Tier-2 (P3) | Tier-2 (P4) | Tier-2 (P3) | N/A | Sedona NDVI / Timber Tags | Taraba rosewood & Mambilla forestry |
| MOD-10 | mod-transport | Transit & Ticketing | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-1 (P4) | Mobile POS / Union Split | Commercial bus/park daily levies |
| MOD-11 | mod-transport-wim | Haulage & Tolls | Tier-2 (P4) | Tier-2 (P4) | N/A | Tier-2 (P4) | N/A | Tier-1 (P4) | Rust Edge / ANPR Cameras | Ogun industrial & Lagos port corridor |
| MOD-12 | mod-agri | Agribusiness | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | N/A | Tier-2 (P4) | N/A | e-Waybill / Warehouse Hubs | Benue food basket & Taraba tea |
| MOD-13 | mod-health | Public Health | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-1 (P4) | PostgreSQL RLS / SHIS Hub | Unified hospital billing & claims |
| MOD-14 | mod-education | Education | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-1 (P4) | Mojaloop Student Fee Portal | State tertiary tuition consolidation |
| MOD-15 | mod-market | Commerce | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-1 (P4) | Stall Titling / Concessions | Osogbo Central & Makurdi markets |
| MOD-16 | mod-police-cad | Public Safety | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-2 (P4) | Tier-1 (P4) | Sedona CAD / Wazuh XDR | State police & 112 command centers |
| MOD-17 | mod-lakehouse | Data Platform | Tier-2 (P5) | Tier-2 (P5) | Tier-2 (P5) | Tier-2 (P5) | Tier-2 (P5) | Tier-1 (P5) | Delta Lake / Flink / Spark | Governor executive IGR intelligence |

*P1–P5 = deployment phases aligned to Release Trains RT-01…RT-05 ([`../release-trains.md`](../release-trains.md)).*
