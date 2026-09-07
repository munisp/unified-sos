# Performance Tuning & Capacity Model — SOS Platform

This is the master tuning reference for the Nigerian State Operating System.
Every per-component file lives in [`deploy/tuning/`](../../deploy/tuning/)
and maps to a concrete helm value or compose override (table below). Ground
rules: production runs on Kubernetes via `infra/helm/sos-platform` (one
release per state tenant); `deploy/docker-compose.yml` is dev/Tier-3
equivalence only. The >1M transactions/sec ambition is a *platform aggregate*
target, anchored on TigerBeetle batching — no single OLTP database or HTTP
gateway tier delivers that alone.

## 1. Capacity model — per-tier TPS budgets

| Tier | Examples | Postgres TPS budget | Gateway req/s | Kafka msg/s | TigerBeetle TPS |
|---|---|---|---|---|---|
| Dedicated (Tier 1) | Lagos, Ogun | 25k (dedicated cluster) | 20k | 200k | 500k–1M+ (5 replicas, 8k batches) |
| Hybrid (Tier 2) | Benue, Taraba | 8k | 8k | 80k | 300k (3 replicas) |
| Shared (Tier 3) | Osun, Nasarawa, + | 3k **per tenant**, 15k cluster ceiling | 2k per tenant (rate-limit enforced) | 30k per tenant | shared cluster, 8k batches |

Budgets assume the sizing in each tuning file (32 vCPU/128 GiB Postgres,
4-worker APISIX, 3-broker Kafka). Per-tenant ceilings in the shared tier are
enforced, not aspirational: APISIX `limit-count`, Kafka quotas, and
`max_connections` headroom reserved for PgBouncer pools.

## 2. Language split — where each runtime earns its keep

| Runtime | Components | Why |
|---|---|---|
| **Go** | Gateways and high-fanout CRUD (`mod-rev-core`, `mod-geospatial-gateway`, control-plane) | Cheap goroutines for tens of thousands of concurrent connections; TigerBeetle Go client for ledger batching |
| **Rust** | Edge ingest — WIM axle telemetry, RFID cargo transit, POS daemon (production target per `edge/README.md`), Martin tile server | Memory-safe, no GC pauses on solar/embedded hardware; io_uring-class I/O at the perimeter |
| **Python** | Domain services (`mod-gis-lands`, `mod-kyc-kyb`, `mod-environment`, ...) + batch ML (Ray valuation, Sedona jobs) | Fastest policy iteration; CPU-bound work lives in batch jobs behind lakehouse SLAs, not on the request path |

Rule: no Python service sits on a hot fan-out path without a Go gateway in
front; no Rust service owns business policy; no ML scoring inline in OLTP.

## 3. Mojaloop database decision record {#mojaloop-database}

Honest answer: **Mojaloop upstream (central-ledger, central-settlement)
officially supports MySQL (via knex's mysql dialect). A Postgres port is
not upstream-supported** — only experimental community forks/dialect branches
exist, with no SLA and real migration risk on upgrades. Therefore:

- **(a) Recommended:** run Mojaloop's internal ledger on the tuned MySQL in
  [`deploy/tuning/my.cnf`](../../deploy/tuning/my.cnf). Mojaloop is an edge
  interoperability switch (NIBSS / Remita / Interswitch / ISO 20022 clearing);
  it is **not** the platform's system of record. TigerBeetle remains the
  sovereign ledger of record (>1M TPS design, VSR quorum, 120-bit
  double-entry), so Mojaloop's database choice has no bearing on ledger
  integrity — reconciliation between the two is by design, not accident.
- **(b) Not recommended / tracked only:** knex/postgres dialect forks for
  central-ledger. If a future upstream release lands Postgres support, adopt
  it then; until that, a fork means owning every schema migration and
  diverging from Mojaloop upgrade paths — unacceptable for a payment switch.

## 4. Horizontal scaling path per component

| Component | Scale unit | Trigger (KEDA/HPA signal) | Ceiling |
|---|---|---|---|
| APISIX | +1 pod (4 workers) | p99 latency, cpu | etcd watch latency |
| Go gateways / mod-rev-core | +1 pod | Kafka lag (existing `keda.scaledObject` on `ng.sos.revenue.assessments`, lagThreshold 1000) | Postgres pool |
| Python modules | +1 pod | queue depth / RPS | DB connections |
| Postgres | read replicas → partition per tenant → dedicated cluster per Tier-1 state | connection saturation, buffer hit ratio | shard by tenant |
| TigerBeetle | 3→5 replicas (tier upgrade); add parallel clusters (new cluster ids) | CPU on replicas | batch size, RTT |
| Kafka/Redpanda | +1 broker, +partitions | ISR shrink, disk | rebalancing cost |
| Redis | role split (cache vs queue), then replicas | eviction rate, AOF lag | noeviction alerts |
| OpenSearch | +1 data node, dedicated masters at 5 | shard count/node > 600 | heap 50% rule |
| Temporal | workers per task queue | `schedule_to_start_latency` p99 > 1 s | history shards (fixed 512) |
| Keycloak | +1 replica, then second cluster shard per state super-group | login p99, cache misses | ~10 realms/cluster |
| Permify | +1 replica (stateless) | check p99 > 10 ms | Postgres write QPS |
| Fluvio | +1 SPU + partitions | per-partition throughput | one core/partition |

## 5. Backpressure patterns

1. **Kafka lag is the universal shock absorber.** Producers never block on
   consumers; KEDA scales consumers off lag (`keda.scaledObject` in
   values.yaml). Alert on consumer-group lag age, not just offset depth.
2. **APISIX rate limits per tenant** (`apisix.yaml`) shed load before it
   reaches Postgres; 429 with Retry-After, never silent drop.
3. **Edge offline-first:** POS terminals buffer ≥5,000 signed transactions
   locally (`edge/README.md`); reconnect bursts are *shaped* (producer linger
   / Fluvio batching), not rejected — the 99.90% sync SLO forbids drops.
4. **PgBouncer queueing:** when Postgres saturates, clients queue at the
   pooler with bounded `query_wait_timeout`; circuit breakers (Dapr
   resiliency) open at 8 consecutive errors rather than retry-storming.
5. **TigerBeetle batch windows:** clients linger up to 10 ms to fill batches;
   under overload the batch window stretches, degrading latency linearly
   instead of collapsing throughput.
6. **Redis queue role = `noeviction`:** memory pressure raises alerts, not
   silent data loss.

## 6. Edge & networking integration points

- **Caddy edge:** optional lightweight edge terminator in front of APISIX at
  sovereign sites: TLS 1.3 termination with automatic per-state certs and
  **HTTP/3 (QUIC)** for lossy rural cellular links — QUIC's 0-RTT and
  connection migration materially improve POS sync on Taraba/Benue corridors.
  Caddy proxies to APISIX over mTLS; WAF and rate limits stay at APISIX.
- **Cilium/eBPF:** the CNI/integration layer — kernel-level networking
  (kube-proxy replacement, per-tenant network policy), and observability
  (Hubble flow logs feeding Wazuh/OpenSearch SOC). eBPF gives per-flow
  visibility without sidecar tax, complementing Dapr mTLS; use Cilium
  bandwidth manager for tenant QoS matching the Tier budgets above.

## 7. Tuning file → helm/compose mapping

| Tuning file | Component | Overrides in helm (`infra/helm/sos-platform/values.yaml`) / compose (`deploy/docker-compose.yml`) |
|---|---|---|
| `deploy/tuning/postgresql.conf` | Postgres 16 + PostGIS | postgres cluster ConfigMap parameters for `global.postgresHost`; compose `postgres` service (mount under `/etc/postgresql/postgresql.conf`) |
| `deploy/tuning/my.cnf` | MySQL (Mojaloop central-ledger) | Mojaloop deployment ConfigMap (`central-ledger` DB); no compose service — external payment switch |
| `deploy/tuning/tigerbeetle.md` | TigerBeetle VSR cluster | `tigerbeetle.replicaCount`, `tigerbeetle.storageClass`/`storageSize`, `tigerbeetle.resources`; compose `tigerbeetle` `--cache-grid` flag |
| `deploy/tuning/redis.conf` | Redis cache/queue roles | redis subchart config; compose `redis` `--appendonly yes` command |
| `deploy/tuning/kafka-server.properties` | Kafka/Redpanda | broker ConfigMap for `keda.scaledObject.kafka.bootstrapServers`; compose `redpanda` flags (`--smp`, `--memory`) |
| `deploy/tuning/fluvio.md` | Fluvio edge streaming | edge cluster chart (SPU counts, topic partitions) — see `edge/README.md` |
| `deploy/tuning/apisix.yaml` | APISIX gateway | `apisix.*` (replicaCount, resources, `tls.secretName`); APISIX `extraConfigConfigMap` |
| `deploy/tuning/keycloak.md` | Keycloak 26 + Dapr | `keycloak.*` (image, `realmImport.realmsConfigMap`); JVM env via deployment env; Dapr resiliency ConfigMaps |
| `deploy/tuning/opensearch.yml` | OpenSearch 2.16 | opensearch subchart config + `OPENSEARCH_JAVA_OPTS`; compose `opensearch` env (`ES_JAVA_OPTS`, `bootstrap.memory_lock`) |
| `deploy/tuning/temporal.md` | Temporal | temporal subchart (`history.numHistoryShards`, worker resources); persistence DSN to tuned Postgres |
| `deploy/tuning/permify.md` | Permify | permify deployment env (database URI, cache TTLs, distributed cache address) |
| `deploy/tuning/lakehouse.md` | Lakehouse (Delta/Flink/Sedona/Ray) | `modules.lakehouse` job resources; Flink `flink-conf.yaml` ConfigMap; MinIO bucket policies |

## 8. Load-test methodology

Acceptance is executable, not narrative — extend the existing gates in
[`tests/gates/`](../../tests/gates/) (`run_gates.py` — stage1/stage2/stage3/
SAT/go-live/DR; JUnit + Markdown evidence per gate):

1. **Baseline (stage2):** k6/locust against APISIX with per-tenant headers;
   assert p99 within SLO (`docs/operations/slo.md`) at 2× expected state load.
2. **Soak (stage3):** 4 h at 1× load with Kafka lag, eviction rate, WAL
   checkpoint spread, and TigerBeetle batch-fill ratio recorded as evidence.
3. **Burst (SAT):** edge-reconnect simulation — 5k buffered POS txns per
   terminal burst through Fluvio→Kafka→mod-rev-core; pass = no drops, sync
   latency < 3 s (edge SLO).
4. **Ledger stress (go-live):** TigerBeetle batch benchmark at 8k transfers/
   batch against the tier replica count; record TPS and p99 latency into the
   gate evidence bundle. Fail the gate if batch fill < 50% — that's a client
   batching bug, not a ledger limit.
5. **Failure injection (dr):** kill 1 of 3 (2 of 5 dedicated) TigerBeetle
   replicas, 1 Kafka broker, 1 Postgres pod during load; gates must still
   pass per `dr_drill.py` conventions. In production (`SOS_ENV=production`)
   any skipped dependency fails the gate — no silent passes.
