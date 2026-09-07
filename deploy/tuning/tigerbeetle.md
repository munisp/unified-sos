# TigerBeetle tuning — VSR cluster, batching, memory, I/O

Scope: the NG State Sovereign Ledger (cluster id 1, `ledger/chart-of-accounts.md`).
Compose dev runs a single replica (`--replica-count=1`); production runs a
replicated VSR cluster on NVMe PVCs (`tigerbeetle.storageClass: sos-nvme` in
`infra/helm/sos-platform/values.yaml`).

## Cluster sizing (VSR quorum)

| Tenancy tier | Replicas | Quorum (quorum_size = ceil((n+1)/2)) | Survives |
|---|---|---|---|
| shared / hybrid | 3 | 2 | 1 replica loss |
| dedicated (Lagos / Ogun) | 5 | 3 | 2 replica losses |

Always odd counts. More than 5 replicas adds replication bandwidth cost
without meaningful durability gain. Pin replicas across availability zones
with pod anti-affinity; one NVMe PVC per replica via volumeClaimTemplates
(hostPath forbidden — see values.yaml).

## Format & start flags

```bash
# One-time per cluster (helm init job; mirrors compose tigerbeetle-init):
tigerbeetle format --cluster=1 --replica=0 --replica-count=3 \
  --replica-count-of-cluster=3 /data/0_0.tigerbeetle

# Per-replica start (production flags vs compose dev defaults):
tigerbeetle start \
  --addresses=10.0.1.11:3000,10.0.1.12:3000,10.0.1.13:3000 \
  --cache-grid=3GiB \        # up from 512MiB dev default; ~75% of the 4Gi limit
  --memory-pool-threads=8 \  # match CPU request; oversubscription stalls VSR
  /data/0_0.tigerbeetle
```

Helm values give 2 CPU / 4 GiB per replica; raise to 4 CPU / 8 GiB on
dedicated tier and set `--cache-grid=6GiB` accordingly. Grid cache is the
dominant memory consumer — keep `--cache-grid` ≤ 75% of the container limit
to leave headroom for the kernel page cache and the message bus.

## Batching guidance (this is where >1M TPS comes from)

TigerBeetle throughput is batch-bound: aim for **~8k transfers per batch**
(max 8189) from `mod-rev-core` (Go TB adapter) and settlement flows.

- **Linked events / chains for statutory split-ledger flows.** The Ogun LUC
  policy (`config/states/ogun`) splits one payment across 4 beneficiary
  accounts INSTANTLY. Model each split as a chain of transfers with the
  `linked` flag set on all but the last event, so the whole statutory split
  commits atomically or not at all. Pending transfers (`flags.pending`) carry
  escrow legs (PPP concessionaire escrow, account 2099); resolve with
  post/void pending transfers referencing the pending id.
- **Idempotency:** `transfer.id` must be deterministic (ULID/hash of the
  upstream assessment id) so KEDA-scaled duplicate deliveries and edge
  offline-sync replays collapse to `exists` results instead of double posts.
- **Batching layer:** the Go gateways batch per-tenant; flush when the batch
  hits 8k events or 10 ms linger, whichever first. Never issue single-event
  requests on the hot path — that is the difference between ~50k and >1M TPS.

## I/O notes (io_uring)

TigerBeetle uses io_uring on Linux for the storage and network data plane.

- Kernel ≥ 5.10 required; 6.x recommended on the sos-nvme nodes.
- AIO fallback: if the kernel sandbox (seccomp/gVisor) blocks io_uring,
  TigerBeetle fails closed at boot — do NOT run it under gVisor/Kata.
- NVMe: disable noop-vs-deadline churn with `mq-deadline` or `none` scheduler
  on the data volume; `discard=async` mount option.
- Never place two replicas' data files on the same physical NVMe device.

## Capacity

A 3-replica cluster on 2 CPU / NVMe sustains ~100–300k TPS with 8k batches;
dedicated-tier 5-replica clusters with 4 CPU each and the cache sizing above
approach the >1M TPS design envelope under batch-saturated clients. The
bottleneck order is: client batching > network RTT between replicas > disk.
