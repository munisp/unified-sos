# Funds-Flow Integrity Guarantee Model

Scope: every flow of funds in the State Operating System — payment received →
statutory split legs → concessionaire escrow → settlement payout — coordinated
by the middleware layer in `ledger/fundsflow/` (Python reference implementation
mirroring the Go `ledger/splits` adapter and the FSPIOP/NIBSS seams in
`services/mod-mobility-switch`).

## What is guaranteed, and how

| Guarantee | Mechanism | Code |
|---|---|---|
| **Atomicity** | Multi-leg moves NEVER use direct double-entry. A PENDING, `flags.linked` TigerBeetle transfer chain reserves funds; the chain is POSTed (commit) or VOIDed (rollback) as a unit. Linked-chain creation is all-or-nothing — a failure on leg N rolls back legs 1..N−1. A posted pending transfer cannot be voided (only a reversal flow), a voided one cannot be posted. | `tigerbeetle_flows.py` (`build_hold_chain`, `InMemoryTBClient.create_transfers`/`post_pending_transfers`/`void_pending_transfers`) |
| **Saga atomicity across steps** | Every flow is a saga `{hold, split_commit, payout, event_publish}` with a compensation per step. State is persisted after every transition; a crashed saga resumes from its last persisted step (`recover_unfinished`) and is driven to a terminal state (COMPLETED / COMPENSATED / FAILED-for-operator). | `saga.py` (`SagaCoordinator`, pluggable store: in-memory default, fail-closed Postgres seam via `SOS_FUNDSFLOW_DSN`) |
| **Idempotency end-to-end** | (1) Deterministic 128-bit transfer IDs = SHA-256(idempotency_key ‖ leg) → retries hit TB's idempotent create, never double-apply. (2) Idempotency middleware: key → canonical request-hash → stored response. Replay returns the original response without re-execution; same key + different payload → 409 (`IdempotencyConflict`). TTL (default 24h) bounds the window. | `tigerbeetle_flows.deterministic_transfer_id`, `idempotency.py` (in-memory default; Redis seam via `SOS_REDIS_URL`, fail-closed in production profile) |
| **No event loss (outbox)** | Domain write + event write commit in ONE store transaction. A crash before commit loses both (never a bare domain write without its event). A crash after commit / before publish leaves an un-acked row that the relay's recovery scan republishes. Delivery is at-least-once; every event carries the flow's idempotency key so consumers dedupe. | `outbox.py` (`OutboxWriter`, `OutboxRelay.publish_pending` — the recovery scan) |
| **Conservation of value** | Integer kobo only. Split legs must sum EXACTLY to the source amount; percentage splits assign the floor-rounding remainder to the CRF leg (remainder-to-CRF rule). Zero/negative legs rejected; double-entry balance checked per leg; federal pass-through account **5001 is never credited** (matches the `validate_packs` guardrail over `config/states/*/policy-pack.json`). Every violation raises AND is appended to a hash-chained audit log. | `invariants.py` (`ConservationEnforcer`) |
| **Tamper evidence** | All invariant decisions (pass and violation) are hash-chained audit events using `services/_shared/hashchain` primitives (import-guarded, local fallback with identical algorithm). Any mutation/deletion/reorder breaks `verify()`. | `invariants.py` (`HashChainAuditLog`) |
| **Reconciliation backstop** | Continuous three-way reconciliation: ledger balances vs outbox/domain view vs upstream settlement references (Mojaloop FSPIOP fulfilments and the NIBSS e-Bills settlement-sheet iterator, seam shapes from `services/mod-mobility-switch/app/adapters/`). Any mismatch → alert event on the bus AND an automatic hold flag on the affected account (fail-safe). | `reconcile.py` |
| **Orchestration with retries** | Temporal workflow definitions (import-guarded `temporalio`): `FundsTransferWorkflow` (hold→split→publish→settle, exponential-backoff retries, compensation on failure) and `SettlementReconciliationWorkflow` (scheduled sweep). In-process fake runner executes the same step graph when temporalio is absent. | `workflows.py` |

## What remains external (explicitly NOT guaranteed by this layer)

* **TigerBeetle cluster durability** — replication factor, quorum, and disk
  durability of the TB cluster itself (deployment concern; the real client is
  selected only via `SOS_TB_ADDRESSES` and fails closed without it in the
  production profile).
* **Scheme-side settlement finality** — Mojaloop/NIBSS settlement finality and
  reversal windows are governed by the schemes; we consume their fulfilment /
  settlement-sheet references and reconcile against them, we do not define them.
* **HSM key custody** — signing-key custody for FSPIOP JWS / NIBSS HMAC secrets
  (HSM/KMS operations) is outside this layer.
* **Postgres/Redis/Kafka operational HA** — backups, failover, and retention of
  the saga store, idempotency store, and event backbone.

## Threat table → control → test evidence

All tests: `python3 -m pytest ledger/fundsflow -q` (52 tests).

| Threat | Control | Test evidence |
|---|---|---|
| Crash after hold | Persisted saga resumes; deterministic IDs make hold replay a no-op; value conserved | `test_crash_injection.py::test_crash_after_hold_recovery_no_double_credit` |
| Crash after partial commit | Linked chain + saga compensation voids the hold; nothing moves; middleware replay returns original result | `test_crash_injection.py::test_crash_after_partial_commit_replay_is_idempotent` |
| Crash before outbox publish | Committed-but-un-acked row republished by relay recovery scan — no event loss | `test_outbox.py::test_crash_between_commit_and_publish_loses_no_event`, `test_crash_injection.py::test_crash_before_outbox_publish_recovery_scan_no_event_loss` |
| Crash before outbox commit | Transaction is atomic: neither domain row nor event persists | `test_outbox.py::test_crash_before_commit_loses_both_sides_atomically` |
| Duplicate webhook delivery | Idempotency middleware executes once, replays original response; balances credited exactly once | `test_crash_injection.py::test_duplicate_webhook_delivery_single_execution` |
| Replay attack (same key, different payload) | Request-hash mismatch → 409; zero balance change | `test_crash_injection.py::test_replay_attack_same_key_different_payload_409_no_movement`, `test_idempotency.py::test_conflicting_payload_same_key_raises_409` |
| Partial split (mid-chain failure) | Linked-chain creation rolls back all legs; saga ends COMPENSATED; all split accounts at zero | `test_crash_injection.py::test_no_partial_split_under_mid_chain_crash`, `test_tigerbeetle_flows.py::test_linked_chain_failure_rolls_back_entire_chain` |
| Event loss / duplicate delivery to consumers | At-least-once relay + consumer dedupe on idempotency key | `test_outbox.py::test_relay_failure_is_retried_at_least_once_with_dedupe_key` |
| Split-sum drift (rounding) | Exact-sum enforcement, remainder-to-CRF | `test_invariants.py::test_compute_split_remainder_to_crf_exact_sum`, `::test_sum_mismatch_raises_and_is_audit_logged` |
| Federal pass-through credited | Account-5001 credit rejected, violation audit-logged | `test_invariants.py::test_federal_pass_through_5001_never_credited` |
| Audit tampering | SHA-256 hash chain over audit events; `verify()` flags mutation | `test_invariants.py::test_hash_chain_integrity_and_tamper_detection` |
| Ledger/upstream divergence | Reconciliation alert + automatic account hold | `test_reconcile_workflows.py::test_ledger_mismatch_alerts_and_holds_account`, `::test_aborted_upstream_fulfilment_alerts` |
| Clock skew | Idempotency TTL bounded window; deterministic (not time-based) transfer IDs; backstop reconciliation | `test_idempotency.py::test_ttl_expiry_allows_fresh_execution` |
| Compensation itself fails | Saga transitions to FAILED for operator intervention; state persisted | `test_saga.py::test_compensation_failure_marks_failed_for_operator` |
| Transient activity failure | Exponential-backoff retry in workflow layer | `test_reconcile_workflows.py::test_workflow_retries_with_backoff_then_succeeds` |
