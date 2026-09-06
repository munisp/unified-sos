# Tier 3 — Shared Multi-Tenant environment (Osun, Benue, Nasarawa, Taraba).
# One shared cluster (12x c6i.4xlarge + 6x r6i.4xlarge, 10 TB NVMe) with
# schema-per-tenant Postgres RLS isolation and per-state KMS keyrings +
# per-state object-storage buckets. Cost allocated pro-rata via Kubecost.

terraform {
  required_version = ">= 1.6"
}

provider "aws" {
  region = "ng-lagos-1"
}

locals {
  tier3_states = ["osun", "benue", "nasarawa", "taraba"]
}

module "cluster_shared" {
  source = "../../modules/sovereign-cluster"

  state_id = "shared-tier3"
  tier     = "shared"
  vpc_cidr = "10.30.0.0/16"

  node_pools = {
    system = {
      instance_type = "c6i.4xlarge"
      min_size      = 12
      max_size      = 24
      disk_gb       = 750
      labels        = { "sos.gov.ng/node-pool" = "shared-system" }
    }
    data = {
      instance_type = "r6i.4xlarge"
      min_size      = 6
      max_size      = 12
      disk_gb       = 1750
      labels        = { "sos.gov.ng/node-pool" = "shared-data" }
    }
  }
}

module "kms_tier3" {
  source   = "../../modules/kms-keyring"
  for_each = toset(local.tier3_states)

  state_id = each.key
  tier     = "shared"
}

module "storage_tier3" {
  source   = "../../modules/object-storage"
  for_each = toset(local.tier3_states)

  state_id    = each.key
  tier        = "shared"
  kms_key_arn = module.kms_tier3[each.key].key_arns["object-storage"]
}

output "cluster_name" {
  value = module.cluster_shared.cluster_name
}

output "state_buckets" {
  value = { for state, mod in module.storage_tier3 : state => mod.bucket_names }
}

output "state_keyrings" {
  value = { for state, mod in module.kms_tier3 : state => mod.keyring_alias_prefix }
}
