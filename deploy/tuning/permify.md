# Permify tuning — fine-grained authorization

Scope: Permify provides relationship-based (ReBAC) authorization for the
module suite, complementing Keycloak (authentication + coarse roles).
Backend: the same Postgres cluster (separate database `permify`).

## Schema & cache tuning

```
PERMIFY_DATABASE_ENGINE=postgres
PERMIFY_DATABASE_URI=postgres://permify:***@pgbouncer.sos-data:6432/permify?sslmode=require
PERMIFY_DATABASE_MAX_OPEN_CONNECTIONS=40     # per replica; via PgBouncer txn mode
PERMIFY_DATABASE_MAX_IDLE_CONNECTIONS=10

# Schema (policy model) cache — schema changes are control-plane events, so
# cache aggressively:
PERMIFY_SCHEMA_CACHE_TTL=1h

# Permission check caches:
PERMIFY_DISTRIBUTED_ENABLED=true             # multi-replica check cache
PERMIFY_DISTRIBUTED_ADDRESS=redis-cache.sos-data:6379   # Role A cache redis
PERMIFY_PERMISSION_CACHE_TTL=5m              # balance revocation latency vs hit rate
```

- Check latency budget: p99 < 10 ms from cache, < 50 ms on miss (Postgres
  recursive CTE walk).
- Snap token / content-based consistency: use snap tokens from write
  responses for read-after-write paths (assessment → approval chains);
  allow stale reads elsewhere via the 5 m cache TTL.

## Bundle with Keycloak roles

- Keycloak remains the source of **roles** (realm roles per state);
  Permify holds **relationships** (parcel→MDA, officer→department,
  appeal→assessment).
- Sync path: a control-plane projector maps Keycloak role assignments to
  Permify relationship tuples (`user:<id>#member@role:<name>`) via realm
  admin events. Never duplicate role logic in both systems — Keycloak
  answers "what is this user", Permify answers "may this user touch this
  record".
- The tenant dimension rides in Permify as `organization` = state tenant id,
  matching `tenant_state_id` everywhere else; RLS in Postgres is the last
  line of defense if a check is bypassed (defense in depth, not either/or).

## Scaling

- Stateless check API: scale replicas horizontally behind the mesh; 2 vCPU /
  2 Gi per replica handles ~5k checks/s with cache hits.
- Watch/write path is the bottleneck at scale: keep relationship writes
  batched (bulk endpoint), and shard the Postgres backend per state
  super-group if write QPS exceeds ~2k sustained.
