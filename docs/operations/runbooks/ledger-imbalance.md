# Runbook: Ledger Imbalance (TigerBeetle)

**Trigger:** ledger reconciliation alert (`sosctl ledger verify` failure),
or mod-rev-core reporting a debit/credit mismatch on a revenue split.

**Blast radius:** revenue collection for one tenant state; statutory
splits may be mis-posted to beneficiary accounts.

1. **Freeze writes for the affected tenant.** Scale the ledger client
   path to read-only: set `REV_CORE_LEDGER_READONLY=1` on mod-rev-core
   (ArgoCD overlay patch) for the affected `sos-<state>` namespace only.
   Do not restart the whole cluster.
2. **Identify the window.** Pull the imbalance report:
   `sosctl ledger verify --tenant <state> --since <last-good-timestamp>`.
   The report lists account ids whose posted debits ≠ credits.
3. **Check for in-flight duplicates.** Look for retried transfers with
   reused ids (client retry without idempotency key). Duplicates are
   safe — TigerBeetle dedupes by transfer id; a true imbalance means a
   client posted one side only (crash between linked transfers).
4. **Repair by compensating entry, never by edit.** Post a balancing
   linked-transfer pair with `flags.linked` to complete the torn
   transfer, referencing the original transfer id in `user_data`. Ledger
   entries are immutable — no UPDATE, no DELETE.
5. **Verify:** re-run `sosctl ledger verify --tenant <state>`; it must
   pass before reopening writes. Re-enable writes, then re-run the
   revenue-split smoke test (`services/mod-rev-core` tests or SAT suite).
6. **Post-incident:** record root cause + compensating transfer ids in
   the incident log; if the imbalance spans a backup boundary, re-run
   `tools/backup/restore_verify.py` for the tenant before the next drill.

**Escalate** to the platform lead if > 100 accounts are imbalanced or the
imbalance touches `PPP_TECH_CONCESSIONAIRE_ESCROW` accounts (Clause 22
concession guardrails).
