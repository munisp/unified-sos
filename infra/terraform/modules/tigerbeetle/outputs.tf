output "cluster_name" {
  description = "Name of the TigerBeetle StatefulSet."
  value       = kubernetes_stateful_set.cluster.metadata[0].name
}

output "client_address" {
  description = "In-cluster address clients (mod-rev-core) use for TB_ADDRESSES."
  value       = "${kubernetes_service.internal_lb.metadata[0].name}.${var.namespace}.svc.cluster.local:3000"
}

output "replica_count" {
  description = "Provisioned VSR replica count (odd, >= 3)."
  value       = var.replica_count
}

output "cluster_id" {
  description = "TigerBeetle cluster ID (TB_CLUSTER_ID)."
  value       = var.cluster_id
}
