variable "state_id" {
  description = "State tenant identifier (lagos|ogun|osun|benue|nasarawa|taraba). Shared-tier deployments pass state_id=\"shared-tier3\"."
  type        = string

  validation {
    condition     = contains(["lagos", "ogun", "osun", "benue", "nasarawa", "taraba", "shared-tier3"], var.state_id)
    error_message = "state_id must be a known SOS tenant."
  }
}

variable "tier" {
  description = "Tenancy tier: dedicated (Tier 1), hybrid (Tier 2) or shared (Tier 3)."
  type        = string

  validation {
    condition     = contains(["dedicated", "hybrid", "shared"], var.tier)
    error_message = "tier must be one of: dedicated, hybrid, shared."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for the TigerBeetle cluster (per-tenant ledger plane)."
  type        = string
}

variable "cluster_id" {
  description = "TigerBeetle cluster ID. 1 = NG State Sovereign Ledger (ledger/chart-of-accounts.md)."
  type        = number
  default     = 1
}

variable "replica_count" {
  description = "TigerBeetle replica count. Must be odd and >= 3 for VSR quorum (dedicated tier: 5, hybrid/shared: 3 per rollout matrix)."
  type        = number
  default     = 3

  validation {
    condition     = var.replica_count >= 3 && var.replica_count % 2 == 1
    error_message = "replica_count must be an odd number >= 3 (VSR quorum)."
  }
}

variable "image" {
  description = "TigerBeetle server image (Apache-2.0). Pinned by tag+ digest at release time."
  type        = string
  default     = "ghcr.io/tigerbeetle/tigerbeetle:0.16.11"
}

variable "storage_size" {
  description = "Per-replica data volume size. TigerBeetle preallocates its data file; size for the tier's clearing volume."
  type        = string
  default     = "100Gi"
}

variable "storage_class" {
  description = "StorageClass for replica PVCs. Must be NVMe-backed (TigerBeetle requires direct-IO capable local storage)."
  type        = string
  default     = "sos-nvme"
}

variable "tags" {
  description = "Additional resource tags. State, tier and cost-centre tags are added automatically."
  type        = map(string)
  default     = {}
}
