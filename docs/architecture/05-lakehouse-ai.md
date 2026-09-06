# Lakehouse Data Platform, Real-Time Streaming & AI/ML

## Medallion Architecture

SOS ingests raw, immutable state data into a three-tier **Delta Lake** lakehouse, combining streaming velocity via **Apache Flink** and **Fluvio** with analytical acceleration via **Apache DataFusion**:

| Layer | Storage Format | Processing Engine | Transformations & Quality |
|---|---|---|---|
| **Bronze** (Raw) | Delta Lake append-only (JSON/Protobuf) | Fluvio / Apache Flink streaming | Raw POS receipt logs, IoT weighbridge packets, GPS telemetry, hospital admission logs |
| **Silver** (Cleansed) | GeoParquet / Snappy-compressed Delta | Apache Spark / Sedona batch ETL | Deduplication, schema validation, geospatial normalization (EPSG:4326), NIN-to-STIN resolution |
| **Gold** (Aggregated) | Pre-aggregated Delta tables & feature stores | Apache DataFusion + Ray AI workers | Executive IGR metrics, tax delinquency risk scoring, automated valuation models (AVM) |

**Acceptance gates (WP-15):** end-to-end streaming ingest latency < 2 s with zero data loss during bursts; sub-second query on Gold tables; revenue-leakage alerts triggered < 60 s.

## Distributed AI/ML on Ray

- **Automated Property Valuation (AVM):** gradient-boosted regression trees (XGBoost on Ray) over building-footprint area, neighborhood infrastructure indices, road access, and recent escrow transaction data → baseline Land Use Charge assessments. Target: >92% R² against certified surveyor valuations.
- **Tax Evasion & Anomaly Scoring:** isolation forests and graph neural networks detecting undeclared business branches and collusion at mineral transit checkpoints.

## Deployment Topology

| Component | Small/Medium State (Tier 2/3: Benue, Taraba, Osun, Nasarawa) | Large State (Tier 1: Lagos, Ogun) |
|---|---|---|
| Kubernetes | Shared multi-tenant EKS/RKE2 (6× 16-core workers) | Dedicated EKS/bare-metal (16× 32-core workers + GPU pools) |
| TigerBeetle | Shared 6-node replica cluster, logical partition IDs | Dedicated 6-node cluster on NVMe bare metal |
| PostgreSQL | Shared PG16 + Patroni HA (schema-per-state) | Dedicated HA cluster (32 vCPU, 128 GB RAM, read replicas) |
| Lakehouse storage | Shared MinIO tenant, dedicated bucket encryption keys | Dedicated petabyte-scale Ceph object storage |

## FinOps with Kubecost

Kubecost runs on all worker nodes; compute, memory, GPU, disk, and egress costs are measured in real time and charged to the State MDA / PPP concessionaire share:

```
cost_center = "state:ogun/mda:bir/module:mod-rev-core"
cost_center = "state:nasarawa/mda:lands/module:mod-gis-lands"
```

100% cloud-cost attribution per state; KEDA scales 2 → 200 pods during morning peaks from Kafka queue depth and TigerBeetle transfer lag.
