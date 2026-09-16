# Deep Adversarial Audit — 2026-09 (Stage 14)

Method: 4 parallel code-grounded auditors (money movement, lands/legal, identity/security,
operations). Every finding was verified against source with file:line evidence — nothing
aspirational. **79 verified findings**: money 17, lands/legal 26, identity/security 16, ops 20.

## Headline scenarios the platform did NOT handle (now being fixed)

### Money movement
1. **Settlement double-pay on crash/retry** — fresh BillID per call + mark-paid after chain commit (CRITICAL)
2. **No reversal/refund flow anywhere** — posted splits irreversible, saga compensations were no-op lambdas (CRITICAL)
3. **Over/underpayment hard-rejected** — no partial payments, no credit balance (CRITICAL)
4. **Reconciliation never compared amounts**; holds in-memory, non-enforced (HIGH)
5. **Mortgage overpayment silently vanished** from the books (HIGH)
6. **Mortgage events published non-transactionally** after money moved (HIGH)
7. **Duplicate NIBSS webhook with different amount silently accepted** (HIGH)
8. **END_OF_MONTH split legs computed but never executed** — concessionaire/LGA shares stranded (HIGH)
9. **Saga recovery couldn't rebuild actions; FAILED state silent** (HIGH)
10. **Split configs validating with totals ≠ 100%** (MEDIUM)
11. **Mobility settlement legs in float, could be zero, never submitted to ledger** (MEDIUM)
12. **Escrow prepare-before-record ordering; no expiry** (MEDIUM)
13. **Idempotency TOCTOU + 24h TTL vs days-later chargebacks** (MEDIUM)
14. **Waterways: non-idempotent tickets/surveys, float royalties** (MEDIUM)
15. **No wrong-bill correction path; no cash/field-agent collection channel** (MEDIUM)

### Lands / legal registry
16. **No title transfer/conveyancing primitive** (sale, gift, assent) (CRITICAL)
17. **No encumbrance register** — caveats, lis pendens; mortgage liens invisible to lands guards (CRITICAL)
18. **Deed verification validated superseded/revoked titles** (CRITICAL)
19. **Subdivision/merger could change ownership without a legal transfer** (CRITICAL)
20. **Mortgage lien registration never verified the title reference** (CRITICAL)
21. **No probate/transmission on death of holder** (CRITICAL)
22. **No court-order vesting/rectification** (CRITICAL)
23. **No governor revocation (LUA s.28) + compensation workflow + ledger payout** (CRITICAL)
24. **Foreclosure ended at a flag** — no possession, sale, title transfer, priority waterfall (CRITICAL)
25. REGISTERED parcels excluded from overlap checks; no segregation of duties in titling;
    no consent expiry; no co-ownership/shares; no leases; no boundary adjustment (HIGH/MED)

### Identity / security / privacy
26. **USSD identity = MSISDN hash only** — SIM-swap owns the citizen; no PIN, no webhook replay protection (CRITICAL)
27. **No segregation of duties in KYC review** (HIGH)
28. **Right to erasure vs WORM** — no per-subject keys / crypto-shredding (HIGH)
29. **No account recovery for lost phone/NIN** (HIGH)
30. **No deceased-citizen status** — verify() keeps attesting the dead (HIGH)
31. **Face/watchlist data has no retention purge tied to warrant expiry** (HIGH)
32. **Admin token compared with ==, actor self-asserted, no admin-access audit** (HIGH)
33. No minors/guardianship; no agent delegation; no breach-notification workflow; PII scrubbing control-plane-only (MED)

### Operations
34. **Caddy on-demand TLS ask endpoint didn't exist — all 37 domains' issuance would fail** (CRITICAL)
35. **Outbox in-memory only — committed events lost on crash; no DLQ/replay** (CRITICAL)
36. **Event consumer dedupe in-memory — duplicate journals after restart** (CRITICAL)
37. **ML drift alerts had no subscriber; registry.rollback() had no caller** (HIGH)
38. **Per-tenant gateway rate limits documented but never rendered** (HIGH)
39. **14/27 Python services without hash-chained audit** (HIGH)
40. UTC-vs-WAT month-boundary booking bug in erp-bridge; out-of-order event delivery; backup CronJobs unscheduled; tenant offboarding absent (MED/HIGH)

## Fix waves (Stage 14)
| Wave | Scope |
|---|---|
| F1 | ledger/fundsflow: reversal chains, real saga compensations + recovery descriptors + FAILED alert, Postgres outbox + DLQ + seq ordering, atomic idempotency + 7d money TTL, EOM sweep runner |
| F2 | mod-rev-core + ledger/splits: idempotent settlement, partial/overpay + credit account, refunds w/ dual control, corrections endpoint, 100% conservation in policy.go + validate_packs.py |
| F3 | mobility (webhook conflict, integer legs, real chain settlement, escrow ordering/expiry), waterways (idempotency, dedupe, integer royalty), erp-bridge (WAT fix, durable consumer dedupe) |
| F4 | mod-gis-lands legal layer: TransferWorkflow, encumbrance register + guard, lifecycle-aware verify, subdivision ownership lock, overlap fix, probate, court orders, revocation + compensation payout, titling SoD |
| F5 | mod-mortgage: title_snapshot verification, overpayment credit/refund, atomic outbox, full foreclosure → possession → sale → priority waterfall → title transfer → closure |
| F6 | identity/security: USSD PIN + replay, KYC four-eyes, face retention purge, deceased status, wallet rebind, admin-token hmac + audit, shared PII scrubber, minors/guardianship |
| F7 | ops: Caddy ask endpoint, drift remediation worker + rollback, APISIX per-tenant rate-limit generator, hash-chain audit in 11 modules |

Residuals documented after wave completion (items deliberately deferred with rationale will be
listed in the completeness scorecard §17).
