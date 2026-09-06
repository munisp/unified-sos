# sosctl package

Python implementation of the `sosctl` command surface specified in
[`../README.md`](../README.md) (WP-01 / EP-CP-01).

## Install & run

```bash
pip install -e tools/sosctl          # or: pip install -e tools/sosctl[dev]
sosctl tenant create --state=nasarawa --tier=shared
```

Without installing, run from the repo root with `PYTHONPATH=tools/sosctl/src`:

```bash
PYTHONPATH=tools/sosctl/src python -m sosctl.cli tenant list
```

## Behavior notes (implementation specifics — spec in ../README.md is authoritative)

- **GitOps bundle** (`tenant create`): writes `k8s/namespace.yaml`,
  `postgres/schema-rls.sql`, `keycloak/realm.json`, `storage/s3-kms.json` into
  `./out/gitops/{state}/` (override with `--out-dir`). Output is deterministic
  and idempotent — unchanged files are left untouched. In production these
  files are committed to `infra/gitops` and reconciled by ArgoCD.
- **Tenant registry** (`tenant list|status|suspend`): local JSON file at
  `./out/registry/tenants.json` (override with `--registry` or
  `SOSCTL_REGISTRY`). Production backend: the control-plane Tenant Operator API
  (`contracts/openapi/control-plane.yaml`). The registry stores metadata only
  — zero citizen PII, zero financial balances.
- **Policy validation** (`policy validate`): JSON Schema draft 2020-12 against
  `contracts/policy-packs/revenue-split.schema.json`, plus guardrails that the
  schema cannot express: INSTANT legs sum ≤ 100%; concessionaire share
  ceilings 8% (Lagos/Ogun) / 15% (agrarian states); TigerBeetle account codes
  in 1000–9999. Exit code 1 with an itemized error list on failure.
- **Policy apply**: validates, then stages to `out/config/states/{state}/policy-pack.json`
  (mirrors production `config/states/`). Refuses when `--state` disagrees with
  the pack's `tenant_state_id`.
- **Ledger bootstrap** (`ledger init-chart`): emits
  `out/ledger/{state}/chart-of-accounts.json` mapping the state CRF onto the
  TigerBeetle chart (`ledger/chart-of-accounts.md`), requiring a gazette
  reference per the 90-day playbook (Days 31–60).

## Tests

```bash
cd tools/sosctl && python3 -m pytest
```
