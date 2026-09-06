# Runbook: Payment Scheme Outage (NIBSS e-Bills / FSPIOP-Mojaloop)

**Trigger:** mod-mobility-switch scheme-adapter alerts — callback
signature failures, transfer timeout rate, or scheme heartbeat loss.

1. **Identify the leg.** NIBSS e-Bills (inbound bill notifications) or
   FSPIOP (P2P transfers via the Mojaloop peer)? Check adapter metrics
   per scheme; the adapters are independent and fail closed separately.
2. **Signature failures (FSPIOP).** A spike of bad `FSPIOP-Signature`
   headers after a peer change usually means the callback secret rotated
   on the scheme side. Coordinate the new secret, update Vault
   `sos/payments/switch` (`FSPIOP_CALLBACK_SECRET`), let the
   ExternalSecret refresh, and roll mod-mobility-switch. If no change was
   announced, treat as a potential attack: keep rejecting (fail-closed)
   and page security.
3. **Transfer timeouts.** In-flight transfers in an unknown state must
   be reconciled, not retried blindly: query the peer for transfer state
   by id before any compensating action. Never fulfil a transfer locally
   without peer confirmation — the ledger entry must match the scheme's
   settlement record.
4. **Ledger consistency.** After the outage clears, run
   `sosctl ledger verify` for affected tenants and reconcile switch-side
   transfer records against TigerBeetle postings (see
   `runbooks/ledger-imbalance.md` for any imbalance found).
5. **NIBSS specifics.** e-Bills mandate downtime windows are published
   by NIBSS; for unannounced outages, queue bill-notification
   acknowledgements and replay in order on recovery. Duplicate
   notifications are deduped by biller reference — replay is safe.
6. **Close-out.** Record affected transfer/bill counts and any manual
   reconciliations in the incident log; attach the ledger-verify output.
