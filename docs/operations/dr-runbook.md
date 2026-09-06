# Disaster Recovery Runbook (Stage 7.D)

Scope: the SOS platform data planes — Postgres (schema-per-tenant + RLS),
TigerBeetle ledger, OpenSearch audit WORM archive, and object storage
(MinIO/S3). Tooling lives in `tools/backup/`; the executable gate is
`tests/gates/dr_drill.py` (run via `make gates GATE=dr`).

## 1. Recovery objectives by tier

| Tier | States | Data plane | RPO | RTO |
|---|---|---|---|---|
| Tier 1 — dedicated | Lagos | Dedicated Postgres + dedicated TB cluster | 15 min (WAL archive + hourly schema dump) | 4 h |
| Tier 2 — hybrid | Ogun | Dedicated schemas, shared control plane | 1 h | 8 h |
| Tier 3 — shared | Osun, Benue, Nasarawa, Taraba | Shared cluster, per-tenant schemas | 24 h | 24 h |
| All tiers | — | OpenSearch audit archive | 24 h (daily WORM snapshot) | 48 h (audit reads degraded, writes buffered) |
| All tiers | — | TigerBeetle ledger | 0 (VSR quorum replication) / 24 h for full-cluster loss (PVC snapshot) | 1 h (single replica) / 8 h (full cluster) |

Rationale: ledger loss is the unacceptable case — VSR quorum makes RPO 0
for any failure short of full-cluster loss; full-cluster restores come
from nightly per-replica PVC snapshots (`tools/backup/tigerbeetle_backup.md`).

## 2. Backup schedule

| Job | Tool | Schedule | Output |
|---|---|---|---|
| Per-tenant Postgres schema dump | `tools/backup/postgres_backup.py --execute` | Tier 1 hourly; Tier 2 6-hourly; Tier 3 daily | `manifest.json` + `tenant_*.dump.pgc`, SSE-KMS upload to S3/MinIO |
| TigerBeetle PVC snapshot | per `tools/backup/tigerbeetle_backup.md` | Nightly | volume snapshot ids + object copies |
| OpenSearch audit snapshot | ISM policy `sos-audit-retention` (ad-hoc: `tools/backup/opensearch_snapshot.py --execute`) | Daily | WORM snapshot in `sos-audit-worm-s3` (S3 Object Lock, COMPLIANCE, 2555 d) |
| Restore verification | `tools/backup/restore_verify.py --execute` | Daily, on the restore host | `restore_verify_report.json` next to the manifest |

## 3. Restore procedures

### 3.1 Postgres (single tenant)

1. Provision the restore target (scratch DB first — never restore over a
   live tenant schema).
2. Fetch the manifest + dump: `aws s3 cp s3://$SOS_BACKUP_S3_BUCKET/postgres/<date>/ ./restore/` (manifest.json lists `tenant_<state>.dump.pgc`).
3. Verify integrity: `python3 tools/backup/restore_verify.py --outdir ./restore` (dry-run hash check, no DB).
4. Full verification: `SOS_BACKUP_VERIFY_DSN=<scratch-dsn> python3 tools/backup/restore_verify.py --execute --outdir ./restore` — restores each dump into the scratch DB and diffs row counts + content hashes against the manifest. Non-zero exit = do **not** proceed with this backup; fall back to the previous manifest.
5. Promote: restore the verified dump into the production cluster schema,
   then re-apply RLS policies from `db/migrations` (migrations are
   idempotent; re-run from the last applied version).
6. Re-enable tenant traffic via ArgoCD sync (`infra/gitops/applications/sos-<state>.yaml`).

### 3.2 TigerBeetle

- Single replica loss: delete pod + PVC, let the StatefulSet recreate;
  VSR repair re-syncs from the quorum. No snapshot needed.
- Full-cluster loss: follow `tools/backup/tigerbeetle_backup.md` §Restore —
  recreate PVCs from per-replica snapshots (ordinal ↔ `--replica` index
  must match), scale up, run `sosctl ledger verify` before reopening.

### 3.3 OpenSearch audit archive

1. Re-register the repository: `OPENSEARCH_URL=... python3 tools/backup/opensearch_snapshot.py --execute --verify-only` (confirms the WORM repo is reachable and last snapshot is `SUCCESS`).
2. Restore indices: `POST _snapshot/sos-audit-worm-s3/<snapshot>/_restore` with `"indices": "sos-audit-*"`.
3. Re-run chain verification (`sosctl audit verify-chain`) per tenant.

## 4. Drill schedule

| Drill | Cadence | Evidence |
|---|---|---|
| Single-tenant Postgres restore (scratch DB, hash diff) | Weekly, rotating tenant | `restore_verify_report.json` + gate evidence dir |
| Full DR gate | Weekly in CI against the restore host | `make gates GATE=dr` evidence under `tests/evidence/dr-*` |
| TigerBeetle full-cluster restore (staging tier) | Quarterly | drill record appended to this runbook's evidence log |
| OpenSearch WORM restore + chain verify | Quarterly | `sosctl audit verify-chain` output |
| Tabletop failover walkthrough (all on-call) | Quarterly | signed attendance + updated runbook diffs |

The DR gate fails closed: in `SOS_ENV=production`, a missing manifest, a
manifest older than `SOS_BACKUP_MAX_AGE_HOURS` (default 24), or a missing
restore-verify report is a FAIL, not a skip.

## 5. Roles & escalation

1. On-call SRE executes section 3; page the platform lead if any hash
   check fails (possible tamper or corruption — treat as a security
   incident per `docs/operations/runbooks/audit-chain-tamper-alert.md`).
2. Restore into production requires dual approval (SRE + state platform
   owner) recorded in the audit log.
3. Post-incident: append the drill/incident record to
   `docs/operations/runbooks/` evidence and update RPO/RTO if the
   measured times exceed the targets above.
