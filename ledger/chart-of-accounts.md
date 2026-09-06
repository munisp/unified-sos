# TigerBeetle Chart of Accounts

## 128-Bit Account Identifier Layout

```
[ Bits 0..15:   State Tenant ID ]
[ Bits 16..31:  MDA Category    ]
[ Bits 32..47:  Account Class   ]
[ Bits 48..127: Unique Entity ID ]
```

## State Tenant IDs (Bits 0..15)

| Code | State |
|---|---|
| 0x0001 | Lagos |
| 0x0002 | Ogun |
| 0x0003 | Osun |
| 0x0004 | Benue |
| 0x0005 | Nasarawa |
| 0x0006 | Taraba |

## Account Classes (Bits 32..47)

| Class | Purpose | Examples |
|---|---|---|
| 1xxx | Clearing & Settlement | 1001 Payer Clearing Account |
| 2xxx | MDA Retention & Program Accounts | 2010 Bureau of Lands Retention · 2020 Local Government Share Pool · 2099 PPP Tech Concessionaire Escrow |
| 3xxx | State Treasury | 3001 State Consolidated Revenue Fund (TSA) |
| 4xxx | Statutory & Trust Funds | 4001 Security Trust Fund (LSSTF-model) · 4002 Transport Union Commission Pool |
| 5xxx | Federal Pass-Through (segregated by construction) | 5001 Federal Royalty Pass-Through — **never** credited to state revenue splits |

## Transfer Codes

| Code | Meaning |
|---|---|
| 101 | Land Title Tax / LUC |
| 102 | MDA Retention leg |
| 103 | Concessionaire Share leg |
| 110 | Mineral levy (state-competent) |
| 120 | Haulage / WIM penalty |
| 130 | Market stallage / micro-levy |
| 140 | Transit ticketing |
| 150 | Hospital / education consolidated billing |

## Ledger Topology

- **Shared tier (Tier 3 states):** 6-node replica cluster, logical partition IDs per state.
- **Dedicated tier (Lagos):** standalone 6-node cluster on NVMe bare metal.
- **Ledger ID:** `1` = NG State Sovereign Ledger.
- Splits execute as **linked atomic transfer chains** — the entire chain commits or nothing does (`Flags.Linked`).

## Bootstrap

Each state tenant initializes its chart of accounts mapped directly to the State's Consolidated Revenue Fund during Days 31–60 of the [90-day playbook](../docs/delivery/90-day-playbook.md), per the gazetted statutory split order.
