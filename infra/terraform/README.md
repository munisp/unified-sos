# SOS Sovereign Infrastructure — `infra/terraform/`

Terraform modules provisioning the sovereign-cloud footprint for each tenancy
tier (see `docs/architecture/06-tenancy-security.md`). No credentials are
stored here — authentication is supplied at runtime by the pipeline
(OpenBao/Vault-backed environment variables per `CONTRIBUTING.md`).

## Modules

| Module | Purpose |
|---|---|
| `modules/sovereign-cluster` | Kubernetes cluster (EKS-class) with tier-sized node pools and isolated VPC |
| `modules/object-storage` | MinIO/S3-compatible buckets per state tenant (cadastre archives, Delta Lake) |
| `modules/kms-keyring` | Per-state KMS keyring + keys (air-gapped for Tier 1) |

## Environments (per tenancy tier)

| Env | Tier | States |
|---|---|---|
| `envs/tier1-dedicated` | Tier 1 | Lagos |
| `envs/tier2-hybrid` | Tier 2 | Ogun |
| `envs/tier3-shared` | Tier 3 | Osun, Benue, Nasarawa, Taraba |

Usage:

```bash
cd envs/tier3-shared
terraform init && terraform plan
```

All resource names carry `var.state_id` / `var.tier` so per-state cost
allocation and Kubecost reconciliation stay auditable.
