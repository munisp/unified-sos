# SAT — Site Acceptance Test Gates (Stage 4)

Scripted, machine-checkable SAT gates per
`docs/procurement/acceptance-framework.md`. Gates **never pass silently**:
when a gate needs live-cluster infrastructure it emits an explicit pytest
`SKIP` locally and **fails closed** when `SAT_ENV=production` is set without
the required live endpoints.

| Gate | What it proves | Local mode | Live mode (required env) |
|---|---|---|---|
| `test_sat_reconciliation_zero_discrepancy` | 10,000-assessment replay reconciles to zero discrepancy against the mod-rev-core in-memory ledger | `SAT_SCALE=local` + Go toolchain (launches `cmd/server` with `REV_CORE_LEDGER=memory`) | `SAT_REV_CORE_URL` (fail-closed in production) |
| `test_sat_offline_pos_replay` | Offline POS batches signed by edge-daemon replay exactly once into mod-market sync (no loss, no double-post) | fully in-process (edge-daemon fixtures → mod-market `/tickets/ingest-edge-batch`) | same — already end-to-end at the wire contract |

Every gate emits a JUnit XML report under `tests/sat/results/` (override with
`SAT_REPORT_DIR`) so the procurement evidence pack can archive per-gate
verdicts independently of the pytest runner.

Run: `make test-acceptance` or `python -m pytest tests/sat -v`.
