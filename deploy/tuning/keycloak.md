# Keycloak 26 tuning + Dapr sidecar notes

Scope: Keycloak multi-realm IAM — one realm per state (`sos-lagos`,
`sos-ogun`, ...), federated with NIMC NIN / CAC registries (Layer 1).
Realm JSON comes from `config/states/<state>/` via the realm-import job
(`keycloak.realmImport` in `infra/helm/sos-platform/values.yaml`; dev import
dir `deploy/keycloak/`).

## JVM opts (Quarkus)

```bash
JAVA_OPTS_APPEND="-Xms2g -Xmx2g -XX:MetaspaceSize=256M \
  -XX:+UseG1GC -XX:MaxGCPauseMillis=200 \
  -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/opt/keycloak/data/dumps"
KC_HTTP_ENABLED=false          # TLS terminated at APISIX edge
KC_HEALTH_ENABLED=true
KC_METRICS_ENABLED=true        # Prometheus scrape
```

Scale memory by realm count: ~+150 MiB heap per actively-used realm.
A shared-tier Keycloak serving 6 state realms needs 4 GiB heap and 2 replicas
min; sticky sessions are NOT required with the distributed caches below.

## Database connection pool

Keycloak persists to the shared Postgres cluster (own database, own
credentials — not the tenant RLS database):

```
KC_DB=postgres
KC_DB_POOL_INITIAL_SIZE=20
KC_DB_POOL_MIN_SIZE=20
KC_DB_POOL_MAX_SIZE=100      # per replica; sum across replicas must stay
                             # well under Postgres max_connections (400)
KC_DB_URL_PROPERTIES=?sslmode=require
```

Front it with PgBouncer in transaction mode like every other Postgres client
(see `postgresql.conf` notes).

## Realm-per-state scaling guidance

- **Realm per state tenant** is the isolation boundary; do not coalesce
  realms for "efficiency" — realm export/import is the tenant
  backup/migration unit (dr-runbook.md).
- Shared-tier ceiling: ~10 active realms per Keycloak cluster before cache
  churn dominates; beyond that, shard states across two Keycloak clusters.
- Dedicated tier (Lagos/Ogun): dedicated Keycloak pair, realm `sos-<state>`
  plus the federation master disabled from write.

## Token lifespan defaults

| Token | Lifespan | Rationale |
|---|---|---|
| Access token | 5 min | short-lived; POS/edge refresh cadence |
| Refresh token (SSO session) | 10 h | one business day for MDA staff |
| Offline token | 30 days | edge sync daemons only, per-device revocation |
| Action tokens (email verify) | 12 h | |

Short access tokens + `KC_FEATURES=token-exchange` keep revocation latency
low for staff offboarding.

## Caching

```
KC_CACHE=ispn
KC_CACHE_STACK=kubernetes      # JGroups discovery via KUBE_PING on the cluster
KC_CACHE_CONFIG_FILE=cache-ispn.xml
```

- `realms` and `users` caches: distributed, owners=2 across replicas.
- `sessions`/`clientSessions`: distributed + persistent sessions
  (`KC_FEATURES=persistent-user-sessions` in KC 26) so pod restarts don't
  log out a whole state.

## Dapr sidecar notes (MDA microservices data plane)

MDA services (`services/mod-*`) run with Dapr sidecars on the state data
plane:

- **Placement:** single Dapr placement service per cluster (3 replicas,
  `sos-system` ns); actor-heavy modules (Temporal-adjacent escrow actors)
  pin placement tables — avoid placement rebalancing storms by keeping
  sidecar upgrades rolling, maxUnavailable=1.
- **mTLS:** `sentry` workload identity ON cluster-wide; per-tenant
  `spiffe://sos.gov.ng/<tenant>` ids; 24 h cert rotation, 2 h clock skew
  tolerance for edge kiosks.
- **Resiliency policies (per-tenant `resiliency.yaml`):**
  - timeout: 10 s service-to-service, 30 s to Postgres state stores
  - retry: exponential 3 attempts (100 ms → 1 s), `DaprRetry` on 5xx only —
    never retry 4xx (KYC fail-closed responses must propagate)
  - circuit breaker: 8 consecutive errors → 30 s open, scoped per
    app-id + tenant header
- Cap sidecar at 250 mCPU / 256 Mi (requests) — sidecars outnumber app
  containers; unbounded sidecars silently eat the node budget.
