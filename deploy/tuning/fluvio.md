# Fluvio edge streaming tuning

Scope: Fluvio carries edge/offline-first telemetry — POS terminal sync batches,
WIM (weigh-in-motion) axle data, RFID cross-border cargo events, ANPR camera
corridor metadata (see `edge/README.md`). Central aggregation is Kafka/Redpanda;
Fluvio sits at the edge/state-ingress boundary.

## SPU sizing

- **Shared tier:** 3 SPUs, 2 vCPU / 4 GiB each; replication factor 2 for
  edge-ingest topics that buffer offline terminals.
- **Dedicated tier (Lagos/Ogun):** 5+ SPUs, 4 vCPU / 8 GiB each, spread across
  AZs; RF=3 on `ng.edge.*` topics.
- SPUs are single-threaded per partition — add SPUs and partitions together;
  a fat SPU with 1 partition caps at one core of throughput.

## Topic partitions

| Topic class | Example | Partitions | Replication | Retention |
|---|---|---|---|---|
| POS sync batches | `ng.edge.pos.sync` | 24 | 2 (3 dedicated) | 72 h |
| WIM axle telemetry | `ng.edge.wim.axle` | 12 | 2 | 24 h |
| RFID cargo transit | `ng.edge.rfid.transit` | 12 | 2 | 72 h |
| ANPR corridor | `ng.edge.anpr.plate` | 16 | 2 | 24 h |

Key every record by `(device_id)` — preserves per-terminal ordering for the
`(device_id, sequence)` dedupe protocol in `edge/edge-daemon` and spreads
load evenly (terminal ids hash well).

## SmartModule notes

- Run **filter-map SmartModules at the edge** to drop malformed/prematurely
  retransmitted frames before WAN egress (rural cellular is metered).
- Keep SmartModules WASM-size < 1 MiB and allocation-free; they run inline on
  the SPU hot path.
- Do dedupe *rejection* upstream in the edge daemon, not as a SmartModule —
  the daemon already holds the SQLite ledger of the last 5,000 signed txns
  (WP-05); a stateless SmartModule cannot see sequence gaps.

## Offline sync backpressure

On cellular reconnect, terminals burst-upload buffered transactions. Set
per-terminal producer batch linger 50 ms / 64 KiB, and let the APISIX-side
rate limits (see `apisix.yaml`) shape aggregate ingress rather than rejecting
terminal bursts — the 99.90% sync SLO in `edge/README.md` forbids dropping
catch-up traffic.
