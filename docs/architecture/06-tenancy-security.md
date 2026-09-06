# Multi-Tenancy, Data Isolation & Security Architecture

## Tenancy Tiers & Isolation Matrix

| Tenancy Model | Target States | Database & Storage Isolation | Compute & Network Isolation | Est. Monthly Infra Cost |
|---|---|---|---|---|
| **Tier 1 — Database-per-Tenant (Dedicated)** | Lagos, Ogun (high volume, ₦100bn+ IGR) | Dedicated Postgres/PostGIS instances, dedicated MinIO buckets, dedicated TigerBeetle cluster | Dedicated K8s node pools with node-affinity, isolated VPC, Cilium policies blocking cross-state egress | $25,000–$60,000 |
| **Tier 2 — Hybrid Scale** | Ogun | Dedicated PostGIS / transactional DB pools; shared Sedona & lakehouse processing | Dedicated node pool + shared analytics | $9,500–$18,000 |
| **Tier 3 — Schema-per-Tenant (Shared Cluster)** | Osun, Benue, Nasarawa, Taraba | Shared HA PostgreSQL with separate schemas (`nasarawa.*`, `benue.*`); shared TigerBeetle with partitioned account IDs | Shared K8s with namespace isolation, resource quotas, Dapr service authorization tokens | $1,800–$4,500 per state |

All Tier-3 isolation is enforced by PostgreSQL **Row-Level Security** keyed on `app.current_state_tenant` (see `db/migrations/0001_cadastre.sql`) and Keycloak multi-realm segregation metered via Kubecost.

## Zero-Trust Ingress Verification Pipeline

```
1. INCOMING REQUEST          2. APISIX GATEWAY           3. KEYCLOAK REALM        4. DAPR / SERVICE MESH
   Citizen Portal               TLS 1.3 Termination         Validate State Realm     PostgreSQL RLS Query
   Mobile App                   OpenAppSec ML WAF           Extract User Roles       TigerBeetle Transfer
   POS Terminal                 Rate Limiting / DDoS        Check MDA Scope          Temporal Execution
   IoT Weighbridge              JWT Header Extraction       Inject Tenant Context    Wazuh Audit Logged
```

## Subnational Cyber Defense Stack (24/7 SOC)

- **Wazuh XDR** — OS integrity monitoring and Kubernetes worker log surveillance across all states.
- **OpenCTI** — threat-intel aggregation on indicators targeting Nigerian banking and government IPs.
- **OpenSearch** — immutable audit-trail repository; 7-year tamper-evident fiscal audit retention.
- **Acceptance (M8.1):** MTTA < 15 minutes for Sev-1 cyber events; 100% endpoint agent telemetry coverage.

## Compliance Anchors

- **NDPA 2023:** mandatory in-country residency in certified Tier-3 Nigerian data centers; strict prohibition of extraterritorial citizen PII transfer; role-based cryptographic masking on biometric and cadastre databases.
- **Keycloak realms:** separate sovereign realm per state, federated with NIMC NIN and CAC; zero cross-realm token leakage (acceptance: sub-50 ms token issuance under 10,000 concurrent sessions).
- **Escrow & continuity:** quarterly sovereign source-code escrow; tripartite bank guarantees; gazetted statutory backing for all concessions.
