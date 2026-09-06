# ADR-002: Financial Ledger Engine — TigerBeetle

**Status:** Accepted · **Domain:** Financial Core

## Decision

**TigerBeetle** (Zig kernel) as the authoritative double-entry financial accounting database tracking 128-bit balances via Viewstamped Replication (VSR).

## Rationale

Purpose-built for financial correctness: deterministic zero-loss accounting, >1M TPS, sub-millisecond atomic settlement. Replaces error-prone SQL `UPDATE balance` patterns that historically enabled reconciliation leakage.

## Consequences

- All monetary movement flows through `ledger/`; multi-leg statutory splits are linked atomic transfer chains.
- Account IDs follow the 128-bit hierarchical layout: `[State (16b) | MDA (16b) | Class (16b) | Entity (80b)]`.

## Tradeoff

Requires explicit client-side batching and custom chart-of-accounts account-number mappings (`ledger/chart-of-accounts.md`).
