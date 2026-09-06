# Work Package Specifications — WP-01 to WP-18

Exhaustive work breakdown for engineering, deploying, and operationalizing the multi-tenant SOS platform. Ready for Jira epic generation, sprint allocation, and vendor SoW mapping.

## RT-01 — Sovereign Foundation & Control Plane

### WP-01: Global Platform Control Plane & Tenant Provisioning Engine
- **Outcome:** instant, zero-downtime onboarding of State tenants and MDA sub-tenants with automated resource provisioning, KMS keyring generation, and policy enforcement.
- **Epics:** EP-CP-01 Multi-Tenant Provisioning Operator · EP-CP-02 Policy Pack Ingestion Engine · EP-CP-03 Tenant Schema Migrator.
- **Key story:** `sosctl tenant create --state=nasarawa --tier=shared` provisions K8s namespaces, Postgres schemas, Keycloak realm, and S3 buckets.
- **APIs/Events:** `POST /control/v1/tenants`, `POST /control/v1/tenants/{id}/policy-packs`; event `ng.sos.tenant.provisioned`.
- **Acceptance:** tenant isolation in < 90 seconds; zero cross-tenant access under default Cilium policies.

### WP-02: Identity & Sovereign Access Management (Keycloak)
- **Outcome:** centralized citizen, business, and civil-servant authentication with biometric NIN/CAC federation and fine-grained MDA RBAC.
- **Epics:** EP-IAM-01 Multi-Realm Keycloak Cluster · EP-IAM-02 NIMC/NIN & CAC Federation Gateway · EP-IAM-03 MDA Hierarchical RBAC.
- **Entities:** TenantRealm, UserIdentity, MDARole, CitizenProfile, AuditSession.
- **Controls:** OIDC/OAuth2 PKCE; mandatory MFA for admin roles; FIDO2 for revenue approval officers.
- **Acceptance:** sub-second NIN authentication; JWT carries cryptographic state tenant ID and MDA boundary claims.

### WP-16: DevSecOps, GitOps & Kubernetes Platform Engineering
- **Outcome:** zero-trust multi-cluster K8s operations, ArgoCD GitOps, KEDA autoscaling, Kubecost tenant FinOps.
- **Epics:** EP-OPS-01 Multi-Cluster K8s (RKE2/EKS) Blueprint · EP-OPS-02 ArgoCD GitOps Tenant Declarations · EP-OPS-03 Kubecost Tenant Billing.
- **Acceptance:** 100% infrastructure-as-code; tenant namespace + service-mesh provisioning via single Git commit.

### WP-17: Cyber Defense, SIEM & Threat Intelligence
- **Outcome:** 24/7 SOC protecting sovereign assets (Wazuh XDR, OpenCTI, OpenSearch).
- **Epics:** EP-SEC-01 OpenAppSec ML-WAF & APISIX Ingress Rules · EP-SEC-02 Wazuh XDR & Compliance Auditing · EP-SEC-03 OpenSearch Immutable Audit Archive.
- **Acceptance:** automated SQLi/API-tamper blocking; zero unauthorized audit-log modification.

## RT-02 — Financial Ledger & Core Revenue

### WP-03: Real-Time Financial Ledger Kernel (TigerBeetle)
- **Outcome:** 100% immutable double-entry correctness across tax collections, concession splits, treasury transfers.
- **Epics:** EP-TB-01 128-Bit Chart of Accounts Engine · EP-TB-02 High-Throughput Go/Rust Client Binding · EP-TB-03 Multi-Leg Split Processor.
- **Schema:** `[State (16b) | MDA (16b) | Class (16b) | Entity (80b)]`.
- **Acceptance:** >1M transfers/sec with zero divergence; atomic 3-way split < 15 ms; zero unbacked debits/credits.

### WP-04: Interoperable Payment & Statutory Clearing Switch (Mojaloop)
- **Outcome:** interoperable switch routing NIBSS, banks, mobile money, POS to state treasury.
- **Epics:** EP-PAY-01 Mojaloop FSPIOP Scheme Adapter · EP-PAY-02 Dynamic NIBSS e-Bill Gateway · EP-PAY-03 Instant Concessionaire Settlement Escrow.
- **Acceptance:** end-to-end settlement < 50 ms (p99); automated NIBSS settlement-sheet reconciliation.

### WP-05: Core Revenue & Automated Assessment Engine (`mod-rev-core`)
- **Outcome:** digitize direct assessment, PAYE, withholding, consumption tax, informal market daily levies across all LGAs.
- **Epics:** EP-REV-01 STIN Master · EP-REV-02 Dynamic Tax Calculation Engine · EP-REV-03 Offline POS Tax Collection Agent.
- **Config:** JSON policy files defining brackets, reliefs, penalty rates, revenue heads.
- **Acceptance:** offline POS caches/signs up to 5,000 transactions, syncing cleanly on restoration; 100,000 automated assessments with zero computational discrepancy against gazetted tax laws.

### WP-18: Document Management, OCR & Records Archiving
- **Outcome:** digitize historical paper land titles, court records, revenue files via distributed OCR; cryptographically signed PDF/A on sovereign object storage.
- **Epics:** EP-DOC-01 MinIO Sovereign S3 Bucket Hierarchy · EP-DOC-02 High-Throughput Tesseract/Trident OCR Pipeline · EP-DOC-03 Cryptographic Signature Engine.
- **Acceptance:** scanned deeds searchable by parcel/owner in < 2 s; zero degradation over 50-year retention.

## RT-03 — Spatial Cadastre & Natural Resources

### WP-06: Cadastral GIS, Land Titling & LUC (`mod-gis-lands` & `mod-gis-luc`)
- **Outcome:** eliminate manual land administration, resolve boundary disputes, automate LUC via satellite footprints.
- **Epics:** EP-GIS-01 PostGIS Cadastral Layer · EP-GIS-02 Temporal e-C-of-O Approval Workflow · EP-GIS-03 Sedona Satellite Footprint LUC Billing.
- **Workflow:** `CadastralTitlingWorkflow` (Surveyor → Town Planning → Attorney General → Governor Digital Signature).
- **Acceptance:** C-of-O cycle from 18 months to < 14 days; automated unassessed-property discovery via satellite overlay; 1M-parcel/footprint join < 5 s.

### WP-07: Natural Resources, Mining & Forestry Provenance (`mod-mining` & `mod-forestry`)
- **Outcome:** capture solid mineral royalties/levies (lithium, columbite, gold) and rosewood export levies; prevent illegal extraction.
- **Epics:** EP-MIN-01 Mine Lease Cadastre & Royalty Calculator · EP-MIN-02 IoT Weighbridge Telematics · EP-FOR-01 Timber RFID Provenance Ledger.
- **Acceptance:** instant royalty/levy calculation from tonnage + assay grade with <1% variance; automated alerts on untagged timber haulage; NDVI canopy disturbance >0.5 ha detected within 72 h of ingest.

### WP-14: Distributed Geospatial Analytics Service (Sedona & PostGIS)
- **Outcome:** high-performance distributed spatial query processing, H3 indexing, remote sensing for all spatial modules.
- **Epics:** EP-GEO-01 Sedona Spark/DataFusion Cluster · EP-GEO-02 GeoParquet Lakehouse Sync · EP-GEO-03 Martin Rust Vector Tile Server.
- **Acceptance:** 1.2M-polygon join in 0.24 s; sub-15 ms vector tiles.

## RT-04 — Sector-Specific Economic Modules

### WP-08: Inter-State Transport, Transit Corridors & Weigh-in-Motion
- **Epics:** EP-TRN-01 High-Speed WIM Axle Sensor Ingestion · EP-TRN-02 ANPR License Plate Recognition · EP-TRN-03 Automated Overload Fine Issuance.
- **Stack:** Rust Fluvio edge agent at weighbridges → APISIX → TigerBeetle fine ledger.
- **Acceptance:** overload detected and fine issued < 3 s at >80 km/h; WIM accuracy ±3% up to 100 km/h all-weather; e-manifest verification < 5 s at mobile checkpoints.

### WP-09: Agribusiness Supply Chain, E-Waybill & Warehouse Receipts
- **Epics:** EP-AGR-01 Produce Inspection & E-Waybill Portal · EP-AGR-02 Agro-Hub Warehouse Receipt Tokenizer · EP-AGR-03 Commodity Collateral Clearing.
- **Acceptance:** waybills verified at borders < 10 s via QR; farmers receive bank credit against e-receipts.

### WP-10: Public Health Billing & Facility Operations (`mod-health`)
- **Epics:** EP-HLT-01 Unified Hospital Fee Billing Hub · EP-HLT-02 Digital Consultation EHR Core · EP-HLT-03 State Health Insurance Claim Engine.
- **Acceptance:** 100% electronic patient fee collection; claims adjudicated < 24 h.

### WP-11: Tertiary Education Consolidated Billing & Bursary (`mod-education`)
- **Epics:** EP-EDU-01 Central Student Bursary Portal · EP-EDU-02 Dynamic Tuition Schedule Manager · EP-EDU-03 Automated Faculty Account Clearing.
- **Acceptance:** real-time student fee clearance; automated university-treasury ↔ state CRF reconciliation.

### WP-12: Commercial Markets & Trade Concessions (`mod-market`)
- **Epics:** EP-MKT-01 Digital Market Stall Cadastre · EP-MKT-02 Automated Micro-Tenancy Billing · EP-MKT-03 POS Trader Agent Network.
- **Acceptance:** >95% collection rate across major urban markets; middleman cash leakage eliminated.

## RT-05 — Lakehouse AI & Advanced Security

### WP-13: Public Safety & Emergency Dispatch CAD (`mod-police-cad`)
- **Epics:** EP-SAF-01 112 Emergency Call CAD Ingestion · EP-SAF-02 Real-Time Unit Dispatch & Patrol GPS · EP-SAF-03 State Incident Threat Heatmap.
- **Note:** full state-police modules gated by constitutional amendment ratification; community vigilante CAD operational immediately.
- **Acceptance:** dispatch latency < 30 s; encrypted tactical channels.

### WP-15: Lakehouse Data Platform, Streaming & AI/ML
- **Epics:** EP-LAK-01 Delta Lake Bronze/Silver/Gold Ingestion · EP-LAK-02 Flink Real-Time Revenue Windowing · EP-LAK-03 Ray AI Automated Property Valuation.
- **Acceptance:** sub-second Gold-table queries; leakage alerts < 60 s; AVM >92% R² vs certified surveyor valuations.
