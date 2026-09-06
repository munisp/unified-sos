variable "state_id" {
  description = "State tenant identifier this keyring belongs to."
  type        = string
}

variable "tier" {
  description = "Tenancy tier. Tier 1 (dedicated) keys are air-gapped: no cross-account or cross-state grants are ever created."
  type        = string

  validation {
    condition     = contains(["dedicated", "hybrid", "shared"], var.tier)
    error_message = "tier must be one of: dedicated, hybrid, shared."
  }
}

variable "key_purposes" {
  description = "Logical key purposes to create within the state keyring."
  type        = list(string)
  default     = ["postgres-tde", "object-storage", "ledger-signing"]
}

variable "deletion_window_days" {
  description = "KMS key deletion window. 30 days for sovereign fiscal keys."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional resource tags."
  type        = map(string)
  default     = {}
}
