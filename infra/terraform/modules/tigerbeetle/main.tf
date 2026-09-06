# TigerBeetle VSR consensus cluster for one SOS tenancy tier
# (ADR-002 / WP-03 / EP-TB-03 / Clause 22.2).
#
# StatefulSet-style deployment: odd replica count (>=3) for VSR quorum,
# one PVC per replica (NVMe-backed, no hostPath — data survives pod
# rescheduling), headless Service for stable replica DNS and an internal
# load balancer for client (mod-rev-core) connections.

terraform {
  required_version = ">= 1.6"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.32"
    }
  }
}

locals {
  name = "sos-${var.state_id}-tigerbeetle"
  labels = merge(
    {
      "app.kubernetes.io/component" = "tigerbeetle"
      "app.kubernetes.io/part-of"   = "sos-platform"
      "sos.gov.ng/tenant-tier"      = var.tier
      "sos.gov.ng/managed-by"       = "terraform"
    },
    var.tags,
  )
  # Comma-separated replica addresses passed to --addresses on every
  # replica, in replica-index order (TigerBeetle requires identical
  # ordering on all replicas).
  replica_addresses = join(",", [
    for i in range(var.replica_count) :
    "${local.name}-${i}.${local.name}-headless.${var.namespace}.svc.cluster.local:3000"
  ])
}

# Headless Service: stable per-replica DNS (<pod>.<svc>.<ns>) used for
# the VSR replica mesh.
resource "kubernetes_service" "headless" {
  metadata {
    name      = "${local.name}-headless"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    cluster_ip = "None"
    selector = {
      "app.kubernetes.io/component" = "tigerbeetle"
      "app.kubernetes.io/instance"  = local.name
    }
    port {
      name        = "replication"
      port        = 3000
      target_port = 3000
    }
  }
}

# Internal load balancer for client traffic (mod-rev-core). Never
# exposed outside the VPC (zero-trust, docs/architecture/06).
resource "kubernetes_service" "internal_lb" {
  metadata {
    name      = "${local.name}-client"
    namespace = var.namespace
    labels    = local.labels
    annotations = {
      "service.beta.kubernetes.io/aws-load-balancer-internal" = "true"
    }
  }

  spec {
    type = "LoadBalancer"
    selector = {
      "app.kubernetes.io/component" = "tigerbeetle"
      "app.kubernetes.io/instance"  = local.name
    }
    port {
      name        = "client"
      port        = 3000
      target_port = 3000
    }
  }
}

resource "kubernetes_stateful_set" "cluster" {
  metadata {
    name      = local.name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    service_name = kubernetes_service.headless.metadata[0].name
    replicas     = var.replica_count

    selector {
      match_labels = {
        "app.kubernetes.io/component" = "tigerbeetle"
        "app.kubernetes.io/instance"  = local.name
      }
    }

    template {
      metadata {
        labels = {
          "app.kubernetes.io/component" = "tigerbeetle"
          "app.kubernetes.io/instance"  = local.name
          "sos.gov.ng/tenant-tier"      = var.tier
        }
      }

      spec {
        # Data comes from the PVC volumeClaimTemplate below — hostPath
        # volumes are forbidden (data must follow the pod).
        termination_grace_period_seconds = 60

        container {
          name  = "tigerbeetle"
          image = var.image

          port {
            name           = "replication"
            container_port = 3000
          }

          # Replica index comes from the StatefulSet pod ordinal.
          env {
            name = "POD_NAME"
            value_from {
              field_ref {
                field_path = "metadata.name"
              }
            }
          }

          command = ["/bin/sh", "-c"]
          args = [join(" ", [
            "REPLICA=$(echo $POD_NAME | sed 's/.*-//') &&",
            "/tigerbeetle start",
            "--cluster=${var.cluster_id}",
            "--replica=$REPLICA",
            "--replica-count=${var.replica_count}",
            "--addresses='${local.replica_addresses}'",
            "/data/tigerbeetle.tigerbeetle",
          ])]

          volume_mount {
            name       = "data"
            mount_path = "/data"
          }

          resources {
            requests = {
              cpu    = "1"
              memory = "2Gi"
            }
            limits = {
              cpu    = "2"
              memory = "4Gi"
            }
          }
        }
      }
    }

    volume_claim_template {
      metadata {
        name = "data"
      }
      spec {
        access_modes       = ["ReadWriteOnce"]
        storage_class_name = var.storage_class
        resources {
          requests = {
            storage = var.storage_size
          }
        }
      }
    }
  }
}
