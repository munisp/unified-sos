# Sovereign Kubernetes cluster for one SOS tenancy tier.
# Isolation model per docs/architecture/06-tenancy-security.md:
#   - dedicated: single-state cluster, isolated VPC, NVMe node pools
#   - hybrid:    dedicated DB node pool + shared analytics plane
#   - shared:    multi-state cluster, namespace + RLS isolation

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
      "sos.gov.ng/tenant-tier" = var.tier
      "sos.gov.ng/managed-by"  = "terraform"
      "sos.gov.ng/cost-centre" = "sos-${var.state_id}"
    },
    var.tags,
  )
}

resource "aws_vpc" "tenant" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true

  tags = merge(local.base_tags, {
    Name = "sos-${var.state_id}-${var.tier}-vpc"
  })
}

resource "aws_subnet" "private" {
  count = 3

  vpc_id            = aws_vpc.tenant.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = "${var.region}${element(["a", "b", "c"], count.index)}"

  tags = merge(local.base_tags, {
    Name = "sos-${var.state_id}-private-${count.index}"
  })
}

resource "aws_eks_cluster" "sos" {
  name     = "sos-${var.state_id}-${var.tier}"
  version  = "1.30"
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = false # zero-trust: private API endpoint only
  }

  tags = local.base_tags
}

resource "aws_eks_node_group" "pools" {
  for_each = var.node_pools

  cluster_name    = aws_eks_cluster.sos.name
  node_group_name = "sos-${var.state_id}-${each.key}"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = [each.value.instance_type]
  disk_size       = each.value.disk_gb
  labels          = each.value.labels

  scaling_config {
    min_size     = each.value.min_size
    max_size     = each.value.max_size
    desired_size = each.value.min_size
  }

  tags = local.base_tags
}

resource "aws_iam_role" "cluster" {
  name = "sos-${var.state_id}-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.base_tags
}

resource "aws_iam_role" "node" {
  name = "sos-${var.state_id}-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.base_tags
}
