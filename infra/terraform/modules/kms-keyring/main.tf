# Per-state KMS keyring. Keys are state-scoped; Tier-1 (dedicated) keys are
# air-gapped — no grants outside the state account are ever created
# (docs/architecture/06-tenancy-security.md, Dedicated State Data Plane).

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  base_tags = merge(
    {
      "sos.gov.ng/tenant-state" = var.state_id
      "sos.gov.ng/tenant-tier"  = var.tier
      "sos.gov.ng/managed-by"   = "terraform"
    },
    var.tags,
  )
}

resource "aws_kms_key" "state" {
  for_each = toset(var.key_purposes)

  description             = "SOS ${var.state_id} ${each.key} key (${var.tier} tier)"
  deletion_window_in_days = var.deletion_window_days
  enable_key_rotation     = true

  tags = local.base_tags
}

resource "aws_kms_alias" "state" {
  for_each = aws_kms_key.state

  name          = "alias/sos-${var.state_id}-${each.key}"
  target_key_id = each.value.key_id
}
