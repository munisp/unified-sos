variable "state_id" {
  description = "State tenant identifier this bucket set belongs to."
  type        = string
}

variable "tier" {
  description = "Tenancy tier: dedicated | hybrid | shared. Dedicated tenants get their own buckets; shared-tier tenants get a bucket prefix on the shared bucket is NOT allowed — every state always receives a distinct bucket, sized differently."
  type        = string

  validation {
    condition     = contains(["dedicated", "hybrid", "shared"], var.tier)
    error_message = "tier must be one of: dedicated, hybrid, shared."
  }
}

variable "kms_key_arn" {
  description = "ARN of the per-state KMS key (from the kms-keyring module) used for bucket default encryption."
  type        = string
}

variable "buckets" {
  description = "Bucket purposes to provision (suffixes appended to sos-<state_id>-)."
  type        = list(string)
  default     = ["cadastre-archives", "delta-lake", "deed-vault"]
}

variable "replication_enabled" {
  description = "Enable async replication to the secondary sovereign region (Delta Lake RPO<15m per resilience doc)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}
