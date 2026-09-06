# TigerBeetle Ledger Backup & Snapshot Notes (Stage 7.D)

TigerBeetle is the double-entry ledger plane (ADR-002 / Clause 22.2): a
3-replica VSR cluster per tier, state on PVCs (`volumeClaimTemplates`,
see `infra/helm/sos-platform/templates/tigerbeetle-statefulset.yaml` and
`infra/terraform/modules/tigerbeetle`).

TigerBeetle has **no SQL dump**. Durability comes from (a) VSR
replication across replicas and (b) snapshotting the replica data files
(the `.tigerbeetle` data file on each PVC). Losing a single replica is
recovered by re-syncing from the quorum; a full-cluster loss requires a
data-file snapshot.

## Invariants

- Replica count is odd and >= 3 (validated by `infra/tests/validate_infra.py`).
- Ledger data lives on PVCs only — `hostPath` is forbidden anywhere under `infra/`.
- Snapshots are **crash-consistent**: TigerBeetle's data file is
  append-only with checksums; a crashed/restarted replica verifies
  its checksums on boot. Quiesce is not required, but snapshotting the
  **primary** during low-traffic windows is preferred.

## Procedure

1. Identify replicas: `kubectl -n sos-<state> get pods -l app.kubernetes.io/component=tigerbeetle`.
2. Snapshot each PVC out-of-band (cloud volume snapshot / Velero
   `--snapshot-volumes`), one PVC per replica, labelled
   `tigerbeetle/<tier>/<date>/replica-<n>`.
3. Copy the snapshot to object storage with the same KMS envelope used by
   `postgres_backup.py` (`S3_KMS_KEY_ID` mandatory — fail-closed).
4. Record a manifest entry: replica index, volume snapshot id, object
   key, sha256 of the data file (computed from a snapshot-mounted clone
   during the DR drill, not inline).
5. A replica **re-sync** (replacing one dead replica) does not need a
   snapshot at all: redeploy the StatefulSet pod with a fresh PVC and the
   same `--replica` index; VSR repair pulls the missing suffix from the
   quorum. Prefer re-sync over snapshot restore whenever the quorum
   survives.

## Restore (full-cluster loss)

1. Recreate the PVCs from the per-replica snapshots (same ordinal ->
   same replica index — `--replica` must match the original).
2. Scale the StatefulSet up; replicas verify checksums and converge via
   VSR view change.
3. Run `sosctl ledger verify` (tools/sosctl) to confirm account/transfer
   invariants before reopening the ledger API.
4. The DR gate (`tests/gates/dr_drill.py`) requires evidence that this
   restore path has been drilled within the drill window.

## Fail-closed notes

- Never snapshot only a subset of replicas: a manifest with fewer than
  the configured replica count is treated as failed evidence by the gate.
- Never restore a data file into a replica index different from the one
  it was snapshotted from.
