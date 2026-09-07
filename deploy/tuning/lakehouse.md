# Lakehouse tuning — Delta Lake / Parquet / Flink / Sedona

Scope: Bronze-Silver-Gold medallion lakehouse on MinIO object storage with
Apache Sedona spatial joins, Apache Flink streaming, Ray for AI valuation
(Layer 3). Batch medallion pipeline deploys as the `lakehouse` module
(`modules.lakehouse` in values.yaml, off by default — jobs, not a server).

## Delta Lake / Parquet

- **File sizing:** target 256 MiB–1 GiB Parquet files; enable auto-optimize
  (`optimizedWrite` + `autoCompact`) on Bronze ingest tables.
- **Compaction:** scheduled OPTIMIZE on Silver/Gold tables after each batch
  window; small-file count is the #1 latency killer for point-in-polygon
  ad-hoc queries.
- **Z-order:** `OPTIMIZE ... ZORDER BY (tenant_state_id, lga_code,
  event_date)` on land/parcel and revenue tables; spatial tables use
  `(tenant_state_id, geohash5)` — Z-order on geohash beats Hilbert curves in
  practice for Sedona range filters because of predicate pushdown into
  Parquet row groups.
- **Retention:** Bronze 90 days (replayable from Kafka within 7 days,
  re-ingestable from source beyond that), Silver 2 years, Gold 7 years to
  match the fiscal audit horizon (see `opensearch.yml` ISM).

## Medallion SLAs

| Layer | Freshness SLA | Notes |
|---|---|---|
| Bronze | ≤ 5 min from Kafka | Flink streaming append |
| Silver | ≤ 15 min | dedupe + conform per tenant |
| Gold | ≤ 1 h (daily for statutory reports) | aggregates feeding mod-transparency and Kubecost pro-rata |

Breach = page per `docs/operations/slo.md`; lag surfaced via Prometheus
`lakehouse_lag_seconds{layer,tenant}`.

## Flink checkpointing

```
execution.checkpointing.interval: 60000          # 1 min; audit-critical streams 30 s
execution.checkpointing.mode: EXACTLY_ONCE
execution.checkpointing.timeout: 10min
state.backend: rocksdb
state.checkpoints.dir: s3://sos-flink-checkpoints/<job>/
execution.checkpointing.incremental: true
restart-strategy: failure-rate
restart-strategy.failure-rate.max-failures-per-interval: 5
restart-strategy.failure-rate.failure-rate-interval: 10min
```

- RocksDB incremental checkpoints to MinIO keep checkpoint cost flat as
  state grows (WIM/RFID dedupe state grows unboundedly without it).
- Buffer timeout 50 ms on high-throughput edge sources; idle-source
  detection ON so cold tenants don't stall watermark progress.
- Kafka sinks: `delivery.guarantee=exactly-once` with 10 min transaction
  timeout, matching broker `transaction.state.log` replication in
  `kafka-server.properties`.

## Sedona / Ray

- Sedona: 8 executors × 4 cores / 8 Gi (dedicated tier double); Kryo
  serializer; spatial partition pruning via `sedona.global.index=true`.
- Ray valuation jobs: run on the batch pool, capped at 50% of cluster —
  never contend with Bronze ingest during month-end assessment windows.
