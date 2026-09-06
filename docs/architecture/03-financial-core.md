# Financial Core — TigerBeetle Ledger & Mojaloop Clearing

## TigerBeetle 128-Bit Ledger Kernel

TigerBeetle is the authoritative single source of truth for all monetary balances across state accounts — replacing error-prone SQL `UPDATE balance` patterns with purpose-built distributed double-entry accounting via Viewstamped Replication (VSR) consensus.

**128-bit account identifier layout:**

```
[ Bits 0..15:  State Tenant ID ]
[ Bits 16..31: MDA Category    ]
[ Bits 32..47: Account Class (1-5) ]
[ Bits 48..127: Unique Entity ID   ]
```

Full account taxonomy: [`ledger/chart-of-accounts.md`](../../../ledger/chart-of-accounts.md).

**Performance profile (acceptance):** 500,000 transfers/sec stress test with zero balance divergence and deterministic audit logging; atomic 3-way split commits in <15 ms; RPO = 0, RTO < 10 s across 3 availability zones.

## Atomic Multi-Party Statutory Split

Revenue leakage frequently occurs during post-payment manual reconciliation. SOS enforces splits **directly at the ledger level** before funds reach commercial settlement. Example — ₦100,000 mineral royalty payment:

```
65% (₦65,000) → STATE CONSOLIDATED REVENUE FUND (TSA)
20% (₦20,000) → MDA RETENTION ACCOUNT
15% (₦15,000) → PPP CONCESSIONAIRE ESCROW
```

Reference implementation (Go, linked-transfer atomic chain): [`ledger/splits/cmd/atomic-split/main.go`](../../../ledger/splits/cmd/atomic-split/main.go).

## Mojaloop Interoperable Payment Switch

Open-source subnational payment hub (FSPIOP / ISO 20022) routing collections from NIBSS Instant Payments (NIP), Remita, Interswitch, mobile money, and POS terminals to state treasury accounts.

**Workflow:**
1. Citizen initiates payment
2. Mojaloop quotes transfer
3. Payer bank commits
4. Mojaloop signals TigerBeetle for split transfer
5. Real-time webhook confirms MDA

**Acceptance:** end-to-end settlement < 50 ms (p99); automated reconciliation against NIBSS settlement sheets; payer-bank → State TSA clearing < 3 seconds with automated 3-way statutory split.

## Triple-Lock Auditability

- **Auditor-General:** real-time, read-only cryptographic audit feed into TigerBeetle — every transaction verifiable instantaneously via dedicated replicas.
- **Accountant-General:** real-time visibility into all bank clearing switch settlements, verifying gross funds credit the State Treasury Single Account (TSA) instantly.
- **Clause 22.2:** the vendor's revenue share is computed and distributed strictly through automated TigerBeetle ledger execution — the vendor never collects, holds, or escrows gross State revenues into any private account prior to statutory deduction.

## Chart of Accounts Bootstrap

Each state tenant initializes its TigerBeetle chart of accounts mapped directly to the State's Consolidated Revenue Fund during Days 31–60 of the 90-day playbook ([`docs/delivery/90-day-playbook.md`](../../delivery/90-day-playbook.md)).
