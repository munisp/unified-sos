# SOS Local Development Stack (`deploy/`)

A single-node, laptop-runnable subset of the SOS platform for local
development and integration testing. Production runs on Kubernetes via
`infra/helm/sos-platform` — one Helm release per state tenant.

## Boot

```bash
cp deploy/.env.example deploy/.env   # obviously-fake dev defaults
make compose-up                      # or: docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps
```

Tear down (volumes preserved; add `-v` to wipe):

```bash
make compose-down
```

## What's included

| Component | Image | Purpose | Endpoint |
|---|---|---|---|
| PostgreSQL 16 + PostGIS 3.4 | `postgis/postgis:16-3.4` | System of record; `db/migrations/*.sql` auto-applied as init scripts on first boot | `localhost:5432` |
| Redis 7 | `redis:7-alpine` | Cache / rate-limit / session store | `localhost:6379` |
| TigerBeetle | `ghcr.io/tigerbeetle/tigerbeetle` | Double-entry revenue ledger (cluster 0, replica 0, auto-formatted by `tigerbeetle-init`) | `localhost:3000` |
| Keycloak 26 | `quay.io/keycloak/keycloak` | SSO / OIDC; imports `deploy/keycloak/realm-sos-dev.json` via `--import-realm` | `localhost:8081` |
| Redpanda (Kafka API) | `docker.redpanda.com/redpandadata/redpanda` | Single-node, ZooKeeper-free Kafka-compatible event bus (much lighter than a full Kafka ensemble) | `localhost:9092` |
| MinIO | `minio/minio` | S3-compatible object store (lakehouse raw zone, document vault) | `localhost:9000` (console `:9001`) |
| OpenSearch 2 (single-node) | `opensearchproject/opensearch` | Audit/search indexes; security plugin disabled for dev only | `localhost:9200` |
| mod-rev-core | built from `services/mod-rev-core/Dockerfile` (context = repo root, needs `ledger/splits`) | Flagship Go revenue service | `localhost:8080/healthz` |
| mod-gis-lands | built from `services/mod-gis-lands/Dockerfile` | Flagship Python cadastral service | `localhost:8000/openapi.json` |
| mod-environment | built from `services/mod-environment/Dockerfile` | v3.0 / ENV-09 FastAPI module: emissions telemetry compliance, permits, deforestation alerts, carbon registry, EIA | `localhost:8010/healthz` |
| mod-citizen-portal | built from `services/mod-citizen-portal/Dockerfile` | v3.0 / CIT-11 FastAPI module: citizen SSO wallet (targets local Keycloak), service requests, e-petitions, payroll audit | `localhost:8011/healthz` |

## Excluded from local (and why)

- **Apache Sedona / Spark cluster** — heavyweight distributed spatial
  analytics; needs a real cluster to be meaningful. PostGIS covers local
  geospatial development. See `docs/architecture/04-geospatial-engine.md`.
- **Ray AI valuation** — GPU-oriented distributed compute; irrelevant for a
  single-node dev loop and very heavy on laptops.
- **Wazuh SIEM/XDR** — security-operations stack; belongs to the production
  cyber-operations tier, not the developer inner loop.
- **APISIX gateway, Kubecost, KEDA, Mojaloop switch** — production
  gateway/cost/autoscaling/payment-switch tiers; run under Helm.

## Mapping to production tiers

| Local (this file) | Production (infra/helm/sos-platform + overlays) |
|---|---|
| Single-node everything | Tier 1 dedicated (Lagos) / Tier 2 hybrid (Ogun) / Tier 3 shared multi-tenant (Osun, Benue, Nasarawa, Taraba) |
| `postgis/postgis:16-3.4` container | Shared or dedicated Postgres+PostGIS cluster (`postgresHost` in values.yaml) |
| Redpanda single-node | Kafka cluster in `sos-data` (KEDA scales consumers on queue depth) |
| Keycloak `sos-dev` realm import volume | Per-state realm ConfigMap applied by the `keycloak-realm-import-job` |
| TigerBeetle single replica | Dedicated (Tier 1) or shared TigerBeetle cluster |
| `.env` dev secrets | OpenBao-issued per-state secrets; NDPA 2023 in-country residency enforced |
