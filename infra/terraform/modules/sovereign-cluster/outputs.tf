output "cluster_name" {
  description = "Name of the provisioned SOS cluster."
  value       = aws_eks_cluster.sos.name
}

output "cluster_endpoint" {
  description = "Private Kubernetes API endpoint."
  value       = aws_eks_cluster.sos.endpoint
}

output "vpc_id" {
  description = "Tenant VPC ID (attach NetworkPolicies/Cilium to this VPC)."
  value       = aws_vpc.tenant.id
}

output "private_subnet_ids" {
  description = "Private subnets available to data-plane workloads."
  value       = aws_subnet.private[*].id
}
