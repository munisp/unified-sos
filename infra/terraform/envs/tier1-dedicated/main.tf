# Tier 1 — Dedicated Sovereign environment (Lagos).
# Sizing per docs/delivery/rollout-matrix.md: 16x c6i.4xlarge + 6x r6i.4xlarge,
# 15 TB NVMe, standalone TigerBeetle. Funded 100% by the Lagos PPP concession.

terraform {
  required_version = ">= 1.6"

  # Remote state backend is configured at pipeline time (sovereign in-country
  # state store). No credentials are committed to this repository.
}

provider "aws" {
  region = "ng-lagos-1" # sovereign in-country region (NDPA 2023 residency)
}

module "kms_lagos" {
  source = "../../modules/kms-keyring"

  state_id = "lagos"
  tier     = "dedicated"
}

module "cluster_lagos" {
  source = "../../modules/sovereign-cluster"

  state_id = "lagos"
  tier     = "dedicated"
  vpc_cidr = "10.10.0.0/16"

  node_pools = {
    system = {
      instance_type = "c6i.4xlarge"
      min_size      = 16
      max_size      = 32
      disk_gb       = 1000
      labels        = { "sos.gov.ng/node-pool" = "lagos-system" }
    }
    data = {
      instance_type = "r6i.4xlarge"
      min_size      = 6
      max_size      = 12
      disk_gb       = 2500 # NVMe-backed for TigerBeetle + Postgres
      labels        = { "sos.gov.ng/node-pool" = "lagos-data" }
    }
  }
}

module "storage_lagos" {
  source = "../../modules/object-storage"

  state_id    = "lagos"
  tier        = "dedicated"
  kms_key_arn = module.kms_lagos.key_arns["object-storage"]
}

output "cluster_name" {
  value = module.cluster_lagos.cluster_name
}

output "buckets" {
  value = module.storage_lagos.bucket_names
}

output "kms_keyring" {
  value = module.kms_lagos.keyring_alias_prefix
}
