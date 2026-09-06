# TigerBeetle Financial Ledger

The authoritative single source of truth for all monetary balances across state accounts (ADR-002). VSR-consensus double-entry accounting; 120/128-bit balances; >1M TPS; RPO=0.

| File | Contents |
|---|---|
| [chart-of-accounts.md](chart-of-accounts.md) | 128-bit account ID layout, account class taxonomy, transfer codes |
| [splits/](splits/) | Importable Go library: policy-pack loader, deterministic kobo split computation, 128-bit account ID builder, `LedgerClient` interface + in-memory fake, atomic linked-chain transfer builder |
| [splits/cmd/atomic-split](splits/cmd/atomic-split/main.go) | Runnable reference demo of the atomic multi-leg statutory revenue split (linked transfer chain) |
| [splits/go.mod](splits/go.mod) | Go module `github.com/munisp/unified-sos/ledger/splits` (zero external dependencies) |

### Using the splits library

```go
pack, _ := splits.LoadPolicyPackFile("contracts/policy-packs/examples/ogun-luc-2026.json")
plan, _ := splits.ComputeSplit(grossKobo, pack.Rules)          // deterministic kobo rounding
chain, _ := splits.BuildAtomicChain(plan, params)              // linked atomic transfer batch
res, _ := ledgerClient.CreateTransfers(chain)                  // all INSTANT legs commit or none do
```

Run the demo without a live TigerBeetle cluster:

```sh
cd ledger/splits && go run ./cmd/atomic-split && go test ./...
```

A production adapter maps `splits.Transfer` onto
`github.com/tigerbeetle/tigerbeetle-go` behind the `splits.LedgerClient`
interface; unit tests run against `splits.InMemoryLedger`.

## Non-Negotiables (Clause 22.2)

1. All revenues clear directly into the State Consolidated Revenue Fund (CRF) / gazetted TSA holding accounts.
2. The concessionaire revenue share is computed and distributed strictly through automated TigerBeetle ledger execution at the end of each clearing cycle.
3. No vendor ever collects, holds, or escrows gross State revenues into any private bank account prior to statutory deduction.
4. Federal mining royalty lines are separated from state-competent levies **by construction** in the account taxonomy.
