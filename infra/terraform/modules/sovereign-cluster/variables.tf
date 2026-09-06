variable "state_id" {
  description = "State tenant identifier (lagos|ogun|osun|benue|nasarawa|taraba). Shared-tier clusters pass state_id=\"shared-tier3\" and isolate per namespace."
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

variable "region" {
  description = "Sovereign cloud region. NDPA 2023 requires in-country residency — use a certified Nigerian Tier-3 data center region."
  type        = string
  default     = "ng-lagos-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the tenant VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "node_pools" {
  description = "Node pool definitions. Defaults match the rollout-matrix sizing per tier; override in the env root."
  type = map(object({
    instance_type = string
    min_size      = number
    max_size      = number
    disk_gb       = number
    labels        = map(string)
  }))
  default = {
    system = {
      instance_type = "c6i.4xlarge"
      min_size      = 3
      max_size      = 16
      disk_gb       = 500
      labels        = { "sos.gov.ng/node-pool" = "system" }
    }
  }
}

variable "tags" {
  description = "Additional resource tags. State, tier and cost-centre tags are added automatically."
  type        = map(string)
  default     = {}
}
