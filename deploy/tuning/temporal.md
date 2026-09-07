# Temporal tuning — durable workflow orchestration

Scope: Temporal drives C-of-O approvals, debt escalation, concession
workflows (Layer 2 of the blueprint). Persistence backend: Postgres (the
shared/dedicated cluster already tuned in `postgresql.conf`).

## Namespace per state tenant

- One namespace per state (`sos-osun`, `sos-benue`, ...) plus `sos-global`
  for cross-state clearing workflows. Namespace = retention + auth boundary;
  aligns with the Keycloak realm-per-state model.
- Retention: 30 days closed-workflow history for approvals; 7 years for
  fiscal-impacting workflows is satisfied by archival events flowing to the
  OpenSearch WORM index (see `opensearch.yml`), not by Temporal history —
  do not keep 7-year histories in Temporal itself, they bloat Postgres.

## History shard count

```
history.numHistoryShards: 512    # dev default is 4 — way too low
```

History shards are fixed at cluster bootstrap and cannot be changed without
rebuilding the cluster. 512 shards comfortably serves all 6+ state tenants
to ~50k concurrent open workflows; overshoot rather than undershoot.

## Worker tuning (per service)

- `maxConcurrentActivityExecutionSize`: 200 per worker pod; poll timeout 60 s.
- `maxConcurrentWorkflowTaskExecutionSize`: 400 (workflow tasks are cheap).
- Sticky queue cache: keep enabled; size 10k workflows.
- KEDA/HPA scaling signal: `temporal_worker_task_slots_available` → 0, and
  per-namespace `workflow_task_schedule_to_start_latency` p99 > 1 s.
- One worker deployment per bounded context (approvals, escalation,
  concessions) — never a monolithic worker; task-queue isolation is the
  blast-radius control.

## Server sizing (shared tier)

| Service | Replicas | CPU | Mem |
|---|---|---|---|
| frontend | 3 | 1 | 1 Gi |
| history | 3 | 2 | 4 Gi |
| matching | 3 | 1 | 1 Gi |
| worker (sys) | 2 | 0.5 | 512 Mi |

## Persistence (Postgres) notes

- Separate Temporal databases: `temporal` and `temporal_visibility`, own
  credentials, through the same PgBouncer (transaction mode).
- `persistence.maxConns`: 50 per history host — sum across Temporal services
  must fit under Postgres `max_connections=400` alongside tenants.
- Visibility: use the SQL visibility store only for dev; production
  visibility queries go to the OpenSearch dual-write
  (`sos-workflow-visibility-*`) to keep heavy list/scan load off Postgres.
- Enable `history.cacheSize: 10000` and `persistence.historyMaxQPS: 10000`
  only after measuring; the checkpoint-heavy WAL profile in
  `postgresql.conf` (max_wal_size=16GB) is sized for Temporal's write pattern.
