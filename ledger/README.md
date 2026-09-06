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

### Production adapter (`splits.TigerBeetleLedger`)

The production adapter lives in
[splits/tigerbeetle.go](splits/tigerbeetle.go) behind the `tigerbeetle`
build tag (the Go client links the native TigerBeetle library, so the
default build and `go test` stay hermetic). Default builds compile a
fail-closed stub instead (`splits/tigerbeetle_stub.go`): selecting the
backend without the tag is a hard startup error, never a silent
fallback.

```sh
go build -tags tigerbeetle ./...                    # production binary
cd ledger/splits && go test ./...                   # hermetic (fake only)
TB_ADDRESSES=10.0.0.1:3000,10.0.0.2:3000,10.0.0.3:3000 TB_CLUSTER_ID=1 \
    go test -tags "tigerbeetle integration" ./...   # live contract run
```

Configuration is fail-closed: `NewTigerBeetleLedgerFromEnv()` requires
both `TB_ADDRESSES` (comma-separated host:port) and `TB_CLUSTER_ID`.
Transfer semantics mirror the fake exactly — `Linked`/`Pending` flags,
`Uint128` limb order, ledger/code/user-data mapping, and result codes
mapped back onto the fake's vocabulary (`exists`, `linked_event_failed`,
`linked_event_chain_open`, `exceeds_credits`, ...). Business accounts
are created with `debits_must_not_exceed_credits` so the cluster
enforces the fake's overdraft rejection; the single bootstrap float
(`WithSeedAccount`) is the only account allowed to carry a negative
balance and is the source of provisioning credits.

The shared contract suite (`RunLedgerContractTests` in
[splits/contract_test.go](splits/contract_test.go)) runs against the
fake on every build and against a live cluster under
`-tags "tigerbeetle integration"`.

### Cluster provisioning

Per tier (rollout matrix / docs/architecture/06):

| Tier | Topology |
|---|---|
| Dedicated (Lagos) | standalone 5-replica VSR cluster in `sos-lagos`, NVMe PVCs |
| Hybrid (Ogun) | dedicated 3-replica cluster on the tenant data node pool |
| Shared (Osun, Benue, Nasarawa, Taraba) | shared 3-replica cluster, partition keyed on state tenant ID |

Provision via `infra/terraform/modules/tigerbeetle` (StatefulSet with
odd replica count ≥ 3, per-replica PVCs — no `hostPath` — headless
Service for the replica mesh and an internal-only load balancer for
clients) or the `tigerbeetle.*` values in the `sos-platform` Helm chart.
Per-tenant connection wiring (`TB_ADDRESSES`/`TB_CLUSTER_ID`) ships as a
`sos-<state>-ledger` ConfigMap in each `infra/k8s/overlays/*-tier` state
manifest. `infra/tests/validate_infra.py` enforces the odd-replica /
PVC / no-hostPath invariants.

Chart-of-accounts bootstrap is idempotent and dry-run by default:

```sh
sosctl ledger init --state=osun              # dry-run: print the accounts
sosctl ledger init --state=osun --apply      # requires TB_ADDRESSES
```

## Non-Negotiables (Clause 22.2)

1. All revenues clear directly into the State Consolidated Revenue Fund (CRF) / gazetted TSA holding accounts.
2. The concessionaire revenue share is computed and distributed strictly through automated TigerBeetle ledger execution at the end of each clearing cycle.
3. No vendor ever collects, holds, or escrows gross State revenues into any private bank account prior to statutory deduction.
4. Federal mining royalty lines are separated from state-competent levies **by construction** in the account taxonomy.
