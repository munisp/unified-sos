# Resilience, Business Continuity & Disaster Recovery

## Recovery Objectives

| Subsystem / Data Class | RPO | RTO | DR Architecture |
|---|---|---|---|
| TigerBeetle Financial Ledger | **RPO = 0** (zero data loss) | RTO < 30 s (kernel < 10 s) | Synchronous Viewstamped Replication across 3 availability zones / data centers |
| PostgreSQL / PostGIS Operational DB | RPO < 5 s | RTO < 3 min | Patroni HA with streaming physical replication and automated Raft failover |
| Delta Lake Lakehouse & Archives | RPO < 15 min | RTO < 1 h | Continuous async bucket replication to secondary sovereign cloud region |
| APISIX Gateway & WAF | RPO = 0 | RTO < 30 s | Stateless active-active |
| Mojaloop Payment Switch | RPO < 1 s | RTO < 1 min | Active-active clearing hub |

## Edge POS & Checkpoint Offline Resiliency

In rural checkpoints across Taraba, Benue, and Nasarawa where cellular coverage is intermittent:

1. POS revenue terminals run an **embedded SQLite/Rust edge daemon**.
2. Revenue tickets and digital tax receipts are **signed locally** using the terminal's hardware secure element (SE).
3. Transactions are cached locally (offline-first; ≥5,000 signed transactions per WP-05 acceptance) and asynchronously synced to the APISIX gateway via **mutual TLS** upon cellular reconnect.
4. Async batch sync rides the Fluvio edge streaming pipeline with offline sync reconciliation.

Design detail: [`edge/README.md`](../../../edge/README.md).

## Risk-Linked Operational Mitigations

| Risk | Architectural Mitigation |
|---|---|
| Transport-union / informal resistance | Union welfare commission auto-splits (3–8%) written directly into TigerBeetle payout rules; union marshals equipped with collection handhelds |
| Political transition & repudiation | Concessions anchored in State House of Assembly gazetted laws; irrevocable standing payment orders (ISPO); sovereign source-code escrow |
| Edge connectivity & power failure | Solar-powered ruggedized POS with offline-first caching (above) |
| Civil-service paper bypass | "Zero-paper" statutory cut-offs; Temporal workflow completion wired into MDA monthly performance scorecards |
| Federal–state jurisdictional friction | State monitoring framed as environmental & infrastructure safety surveillance; real-time analytics shared with federal counterparts |
