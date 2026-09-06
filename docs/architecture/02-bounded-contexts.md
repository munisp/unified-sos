# Domain Architecture, Bounded Contexts & Capability Map

SOS decomposes subnational administration into **six business domains** over a **common operating system foundation**.

## The Six Domains

### 1. Revenue & Fiscal Administration
- Direct assessment & PAYE (BIR) · POS informal market micro-levies · dynamic fee & fine assessment · real-time concession escrow split
- **Modules:** `mod-rev-core`, `mod-rev-pos`

### 2. Lands & Spatial Cadastre
- GIS cadastral parcel mapping · automated C-of-O titling workflows · Land Use Charge (LUC) valuation · deed registry & mortgage escrow
- **Modules:** `mod-gis-lands`, `mod-gis-luc`

### 3. Natural Resources & Mining
- Solid minerals cadastre & assay · biometric artisanal miner registry · forestry timber tagging (RFID) · satellite deforestation alerts
- **Modules:** `mod-mining`, `mod-forestry`

### 4. Transit & Mobility Ops
- Multimodal transit clearing hub · Weigh-in-Motion (WIM) axle audit · port logistics e-call-up · inland waterways jetty ticketing
- **Modules:** `mod-transport`, `mod-transport-wim`, `mod-mobility-switch`

### 5. Agriculture & Trade
- Produce waybill e-inspection · warehouse receipts & commodity OS · cocoa/tea traceability passports · cross-border freight telematics
- **Modules:** `mod-agri`, `mod-agri-waybill`, `mod-trade-corridor`

### 6. Health, Education & Social
- Hospital unified e-billing & EHR · state health insurance (SHIA/NHIS) claims · tertiary university central bursary · social welfare disbursement
- **Modules:** `mod-health`, `mod-education`

### 7. Common Operating System Foundation & Control Plane
| Financial Core | Spatial Analytics | Lakehouse | Platform Security & Ops |
|---|---|---|---|
| TigerBeetle + Mojaloop | PostGIS + Apache Sedona | Delta Lake + Flink + DataFusion | Keycloak + Wazuh + Kubecost |

Plus **Public Safety** (`mod-police-cad` — all 6 states, gated by constitutional ratification) and **Identity & Data** and **PPP & Investment** platform services.

## Canonical Module Inventory

| Module | Subsystem | Description | Primary Stack | Deploying States |
|---|---|---|---|---|
| `mod-rev-core` | Revenue Admin Core | STIN taxpayer ID, direct assessment, consumption tax, POS collector sync | Go / Postgres / TigerBeetle | All 6 (common core) |
| `mod-gis-lands` | Land Administration | Cadastral parcels, e-C-of-O, boundary dispute audit | PostGIS / Sedona / Temporal | Nasarawa (Karu), Lagos, Benue, Ogun, Osun, Taraba |
| `mod-gis-luc` | Property Taxation | Automated LUC via satellite footprints, building valuation billing | Sedona / DataFusion / Ray | Ogun, Benue, Lagos, Nasarawa |
| `mod-mining` | Natural Resources | Mineral cadastre, royalty tonnage verification, weighbridge IoT | Rust / Kafka / PostGIS | Nasarawa, Osun, Taraba |
| `mod-forestry` | Environment | Timber/rosewood RFID provenance, deforestation change detection | Python / Sedona / OpenCTI | Taraba, Ogun, Osun |
| `mod-transport-wim` | Mobility | High-speed WIM axle enforcement, automated overload billing | Rust / Fluvio / TigerBeetle | Ogun, Lagos, Nasarawa |
| `mod-agri-waybill` | Agriculture | Produce e-inspection, haulage waybill checkpoint verification | Go / Dapr / Redis | Benue, Nasarawa, Taraba |
| `mod-mobility-switch` | Mobility & Transit | Multimodal contactless transit switch (Cowry-compatible) | Go / Mojaloop / TigerBeetle | Lagos, Ogun |
| `mod-health` | Public Health | Tertiary hospital billing, drug inventory, SHIA/NHIS claims | Go / Postgres / Keycloak | Nasarawa, Osun, Benue |
| `mod-police-cad` | Public Safety | CAD, incident tracking, geofenced patrols | Rust / PostGIS / OpenCTI | All 6 (gated by ratification) |
| `mod-education` | Education | Tertiary consolidated billing & bursary | Go / Postgres | Osun |
| `mod-market` | Commerce | Market stall cadastre, micro-tenancy billing, trader POS network | Go / Redis / TigerBeetle | Osun, Nasarawa |

## Hard Constraint — Federal Royalty Boundary

Mining royalties accrue to the federation account [LIVE]. The extractives data model **separates state-competent levies from federal royalty lines by construction**, so no deployment can accidentally claim a royalty share. See `ledger/chart-of-accounts.md`.
