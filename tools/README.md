# Tools — sosctl CLI

`sosctl` is the platform operator CLI for tenant lifecycle management (WP-01 / EP-CP-01).

## Command Surface

```bash
# Provision a state tenant (< 90 seconds, zero cross-tenant access)
sosctl tenant create --state=nasarawa --tier=shared

# Provisioned: K8s namespace, Postgres schema (RLS), Keycloak realm,
#              S3 bucket, KMS keyring — via GitOps commit to infra/gitops

# Ingest / validate a dynamic policy pack
sosctl policy apply  --state=ogun --file=config/states/ogun/policy-pack.json
sosctl policy validate --file=config/states/ogun/policy-pack.json \
  --schema=contracts/policy-packs/revenue-split.schema.json

# Tenant lifecycle
sosctl tenant list
sosctl tenant status --state=lagos
sosctl tenant suspend --state=taraba --reason="concession breach"

# Ledger bootstrap (Days 31-60 playbook): chart of accounts mapped to CRF
sosctl ledger init-chart --state=benue --gazette-ref="BIRS-EDICT-2026-04"
```

## Design Rules

- Every mutation produces an ArgoCD GitOps commit — 100% infrastructure-as-code (WP-16 acceptance).
- All operations are audited to the OpenSearch immutable archive (7-year retention).
- Control plane holds zero citizen PII; `sosctl` cannot read tenant data plane contents.
