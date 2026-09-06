# Tier 2 — Hybrid Industrial environment (Ogun).
# Dedicated DB / node pool (8x c6i.2xlarge + 4x r6i.2xlarge, 5 TB NVMe) with
# shared Sedona/lakehouse analytics. Funded by the Ogun PPP concession.

terraform {
  required_version = ">= 1.6"
}

provider "aws" {
  region = "ng-lagos-1"
}

module "kms_ogun" {
  source = "../../modules/kms-keyring"

  state_id = "ogun"
  tier     = "hybrid"
}

module "cluster_ogun" {
  source = "../../modules/sovereign-cluster"

  state_id = "ogun"
  tier     = "hybrid"
  vpc_cidr = "10.20.0.0/16"

  node_pools = {
    system = {
      instance_type = "c6i.2xlarge"
      min_size      = 8
      max_size      = 16
      disk_gb       = 500
      labels        = { "sos.gov.ng/node-pool" = "ogun-system" }
    }
    data = {
      instance_type = "r6i.2xlarge"
      min_size      = 4
      max_size      = 8
      disk_gb       = 1250
      labels        = { "sos.gov.ng/node-pool" = "ogun-data" }
    }
  }
}

module "storage_ogun" {
  source = "../../modules/object-storage"

  state_id    = "ogun"
  tier        = "hybrid"
  kms_key_arn = module.kms_ogun.key_arns["object-storage"]
}

output "cluster_name" {
  value = module.cluster_ogun.cluster_name
}

output "buckets" {
  value = module.storage_ogun.bucket_names
}

output "kms_keyring" {
  value = module.kms_ogun.keyring_alias_prefix
}
