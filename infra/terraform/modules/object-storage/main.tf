# Per-state object storage (MinIO/S3-compatible).
# Buckets are always dedicated per state — even on Tier 3 — because cadastre
# deed archives and Delta Lake layers carry NDPA-regulated citizen data.

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

resource "aws_s3_bucket" "state" {
  for_each = toset(var.buckets)

  bucket = "sos-${var.state_id}-${each.key}"

  tags = local.base_tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  for_each = aws_s3_bucket.state

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  for_each = aws_s3_bucket.state

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  for_each = aws_s3_bucket.state

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled" # tamper-evident fiscal audit retention (7-year archive)
  }
}
