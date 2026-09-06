# TigerBeetle Financial Ledger

The authoritative single source of truth for all monetary balances across state accounts (ADR-002). VSR-consensus double-entry accounting; 120/128-bit balances; >1M TPS; RPO=0.

| File | Contents |
|---|---|
| [chart-of-accounts.md](chart-of-accounts.md) | 128-bit account ID layout, account class taxonomy, transfer codes |
| [splits/atomic_split.go](splits/atomic_split.go) | Reference implementation: atomic multi-leg statutory revenue split (linked transfer chain) |
| [splits/go.mod](splits/go.mod) | Go module for the reference client |

## Non-Negotiables (Clause 22.2)

1. All revenues clear directly into the State Consolidated Revenue Fund (CRF) / gazetted TSA holding accounts.
2. The concessionaire revenue share is computed and distributed strictly through automated TigerBeetle ledger execution at the end of each clearing cycle.
3. No vendor ever collects, holds, or escrows gross State revenues into any private bank account prior to statutory deduction.
4. Federal mining royalty lines are separated from state-competent levies **by construction** in the account taxonomy.
