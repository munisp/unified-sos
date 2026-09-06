# State Policy Packs — `config/states/`

Per-state dynamic policy packs: the only place state variation lives
(canonical-core / 80/20 rule, `CONTRIBUTING.md`). Each state directory holds:

| File | Purpose |
|---|---|
| `policy-pack.json` | Statutory revenue-split rules, validated against [`contracts/policy-packs/revenue-split.schema.json`](../../contracts/policy-packs/revenue-split.schema.json) and enforced atomically at the TigerBeetle ledger layer |
| `modules.yaml` | Enabled `mod-*` modules + rollout wave (per `docs/delivery/rollout-matrix.md` and state profiles) |
| `README.md` | State profile summary and applicable modules |

## States & tenancy tiers

| State | Tier | Rollout wave | Concession revenue-share ceiling |
|---|---|---|---|
| Lagos | Tier 1 — Dedicated | Wave 0/1 | 8% |
| Ogun | Tier 2 — Hybrid | Wave 1 | 8% |
| Osun | Tier 3 — Shared | Wave 1 | 15% |
| Benue | Tier 3 — Shared | Wave 2 | 15% |
| Nasarawa | Tier 3 — Shared | Wave 1 | 15% |
| Taraba | Tier 3 — Shared | Wave 2/3 | 15% |

Ceilings per procurement guardrails (agrarian/extractive states ≤ 15%;
Lagos/Ogun ≤ 8%; `docs/procurement/`).

## Validate

```bash
pip install pyyaml jsonschema
python3 config/states/validate_packs.py   # exits 0 on success
```

## Rules for editing a pack

- Never hard-code these values into services; packs are injected at runtime.
- INSTANT legs must sum to ≤ 100; the remainder stays in the payer clearing
  account (1001) until END_OF_MONTH sweeps.
- Account code 5001 (Federal Royalty Pass-Through) must **never** appear in a
  split — it is segregated by construction (`ledger/chart-of-accounts.md`).
- Pack changes require CODEOWNERS review and a gazette reference
  (`gazette_reference` is mandatory).
