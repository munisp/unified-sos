"""K8s namespace operator — namespace + default-deny NetworkPolicy +
ResourceQuota + tenant labels, via the kubernetes Python client.

Fail-closed: requires the optional ``kubernetes`` dependency and either
``KUBECONFIG`` or ``K8S_IN_CLUSTER=true``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import OperatorUnavailableError, ProvisionError

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant

try:  # optional dependency
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    from kubernetes.client.rest import ApiException as K8sApiException

    _K8S_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    k8s_client = None  # type: ignore[assignment]
    k8s_config = None  # type: ignore[assignment]
    K8sApiException = Exception  # type: ignore[assignment,misc]
    _K8S_AVAILABLE = False


class K8sNamespaceOperator:
    name = "namespace"

    def __init__(self, kubeconfig: str | None = None, in_cluster: bool = False) -> None:
        if not _K8S_AVAILABLE:
            raise OperatorUnavailableError(
                "kubernetes client library is not installed (pip install kubernetes)"
            )
        if not in_cluster and not kubeconfig:
            raise OperatorUnavailableError(
                "missing required configuration: KUBECONFIG or K8S_IN_CLUSTER=true"
            )
        try:
            if in_cluster:
                k8s_config.load_incluster_config()
            else:
                k8s_config.load_kube_config(config_file=kubeconfig)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise OperatorUnavailableError(f"kubernetes configuration failed: {exc}") from exc
        self._core = k8s_client.CoreV1Api()
        self._net = k8s_client.NetworkingV1Api()

    def _labels(self, tenant: "Tenant") -> dict[str, str]:
        return {
            "app.kubernetes.io/managed-by": "sos-control-plane",
            "sos.gov.ng/tenant-id": tenant.tenant_id,
            "sos.gov.ng/state": tenant.state,
            "sos.gov.ng/tier": tenant.tier,
        }

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str:
        ns = resources.k8s_namespace
        labels = self._labels(tenant)
        try:
            self._core.create_namespace(
                k8s_client.V1Namespace(
                    metadata=k8s_client.V1ObjectMeta(name=ns, labels=labels)
                )
            )
        except K8sApiException as exc:
            if exc.status != 409:  # upsert semantics: already exists is fine
                raise ProvisionError(f"namespace create failed (status {exc.status})") from exc
        # Default-deny NetworkPolicy (upsert).
        policy = k8s_client.V1NetworkPolicy(
            metadata=k8s_client.V1ObjectMeta(name="default-deny", namespace=ns, labels=labels),
            spec=k8s_client.V1NetworkPolicySpec(
                pod_selector=k8s_client.V1LabelSelector(match_labels={}),
                policy_types=["Ingress", "Egress"],
            ),
        )
        quota = k8s_client.V1ResourceQuota(
            metadata=k8s_client.V1ObjectMeta(name="tenant-quota", namespace=ns, labels=labels),
            spec=k8s_client.V1ResourceQuotaSpec(
                hard={"requests.cpu": "4", "requests.memory": "8Gi", "pods": "50"}
            ),
        )
        try:
            for kind, body, create, replace in (
                ("networkpolicy", policy, self._net.create_namespaced_network_policy,
                 self._net.replace_namespaced_network_policy),
                ("resourcequota", quota, self._core.create_namespaced_resource_quota,
                 self._core.replace_namespaced_resource_quota),
            ):
                try:
                    create(ns, body)
                except K8sApiException as exc:
                    if exc.status != 409:
                        raise
                    replace(ns, body.metadata.name, body)
        except K8sApiException as exc:
            raise ProvisionError(f"namespace policy/quota failed (status {exc.status})") from exc
        return "namespace-created"

    def decommission(self, tenant_id: str) -> None:
        # Namespace names are derivable from tenant metadata; delete by label.
        namespaces = self._core.list_namespace(
            label_selector=f"sos.gov.ng/tenant-id={tenant_id}"
        )
        for ns in namespaces.items:
            try:
                self._core.delete_namespace(ns.metadata.name)
            except K8sApiException as exc:  # pragma: no cover
                if exc.status != 404:
                    raise
