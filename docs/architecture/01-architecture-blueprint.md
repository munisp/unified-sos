# Architecture Blueprint — Topology & Stack Decomposition

## Layer 1: Unified Perimeter, Security & Sovereign IAM

| Component | Role |
|---|---|
| Apache APISIX Gateway + OpenAppSec ML-WAF | Zero-day API threat protection, TLS 1.3 termination, rate limiting, DDoS defense (~1.2 ms overhead) |
| Keycloak Multi-Realm IAM | Sovereign identity, one realm per state (`sos-lagos`, `sos-ogun`, …), federated with NIMC NIN and CAC registries |
| Wazuh XDR + OpenCTI + OpenSearch | Centralized state Security Operations Center, threat intel, immutable audit archive |

## Layer 2: Real-Time Financial Ledger & Interoperable Clearing

| Component | Role |
|---|---|
| TigerBeetle (Zig kernel) | 120-bit double-entry financial kernel; >1M TPS; sub-millisecond atomic settlement via Viewstamped Replication (VSR) |
| Mojaloop (FSPIOP / ISO 20022) | Payment clearing engine interfacing NIBSS, Remita, Interswitch, and commercial banks; atomic multi-leg statutory splits |
| Temporal | Fault-tolerant durable workflow orchestration (C-of-O approvals, debt escalation, concessions) |

## Layer 3: Distributed Geospatial Intelligence & Lakehouse

| Component | Role |
|---|---|
| Apache Sedona + SedonaDB (DataFusion) | Distributed spatial joins (0.24 s polygon evaluations), NDVI remote-sensing change detection |
| PostGIS | Operational transactional spatial CRUD, point-in-polygon verification |
| Martin Tile Server (Rust) | Mapbox Vector Tiles directly from PostGIS (<15 ms) |
| Delta Lake + Apache Flink + Ray | Bronze-Silver-Gold medallion lakehouse, real-time streaming, AI property valuation |

## Layer 4: Modular Subnational Application Suite (20+ modules)

`mod-rev-core` · `mod-gis-lands` · `mod-gis-luc` · `mod-mining` · `mod-forestry` · `mod-transport-wim` · `mod-agri-waybill` · `mod-mobility-switch` · `mod-health` · `mod-education` · `mod-market` · `mod-police-cad` — all provisioned per state via dynamic policy packs.

See [02-bounded-contexts.md](02-bounded-contexts.md) for the canonical module inventory.

## Control Plane vs Data Plane Segregation

| Layer | Components | Operational Ownership | Data Boundary |
|---|---|---|---|
| **Global Control Plane** | SOS Tenant Operator, Dynamic Policy Repository, Global Keycloak Realm Master, ArgoCD GitOps, Kubecost Master | Central PPP Platform Engineering Team only | Contains **zero** citizen PII or financial balances — only metadata, tenant configs, deployment manifests |
| **State Data Plane (Shared Tier)** | MDA microservices (Dapr), Keycloak sub-realms, Kafka, Postgres RLS, TigerBeetle partitions | Joint State Digital Transformation Office & PPP Operations | Encrypted at rest with state-specific KMS keys; RLS + namespace isolation |
| **State Data Plane (Dedicated Tier)** | Dedicated K8s cluster, isolated Postgres, standalone TigerBeetle cluster, dedicated MinIO buckets | Dedicated State Sovereign Cloud Team (Lagos / Ogun) | Physical + cryptographic isolation; dedicated VPCs; air-gapped keyrings |

## Canonical Configuration Engine & Dynamic Policy Packs

State adaptation happens through **Dynamic Policy Packs** (JSON / OPA Rego). Example — Ogun Land Use Charge statutory distribution policy:

```json
{
  "tenant_state_id": "ogun",
  "policy_id": "POL_REVENUE_SPLIT_LUC_2026",
  "revenue_head": "REV_LAND_USE_CHARGE",
  "statutory_split_rules": [
    { "beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND",   "tigerbeetle_account_code": 3001, "split_percentage": 75.0, "deduction_timing": "INSTANT" },
    { "beneficiary": "OGUN_BUREAU_OF_LANDS_RETENTION",   "tigerbeetle_account_code": 2010, "split_percentage": 15.0, "deduction_timing": "INSTANT" },
    { "beneficiary": "PPP_TECH_CONCESSIONAIRE_ESCROW",   "tigerbeetle_account_code": 2099, "split_percentage": 8.0,  "deduction_timing": "INSTANT" },
    { "beneficiary": "LOCAL_GOVERNMENT_SHARE_POOL",      "tigerbeetle_account_code": 2020, "split_percentage": 2.0,  "deduction_timing": "END_OF_MONTH" }
  ]
}
```

Schema: [`contracts/policy-packs/`](../../../contracts/policy-packs/README.md). Live packs: [`config/states/`](../../../config/states/README.md).

## Supplemental Open-Source Infrastructure (Air-Gap Ready)

| Subsystem | Tool | Rationale |
|---|---|---|
| Object storage | MinIO / Ceph | Delta Lake layer + encrypted deed archives; S3 API portability |
| Secrets & keys | OpenBao / Vault | Sovereign envelope encryption, per-state master keys |
| Vector tiles | Martin (Rust) | MVT straight from PostGIS |
| Autoscaling | KEDA | Kafka queue depth & TigerBeetle lag driven pod scaling |
| Tracing | OpenTelemetry + Jaeger | Vendor-agnostic tracing across polyglot services |
| Metrics | VictoriaMetrics | Millions of POS/IoT timeseries at 10× less RAM |
| Image registry | Harbor | Trivy scanning + Cosign signing, multi-tenant |
