"""Validation gates for deploy/cilium/ (run with: pytest deploy/cilium/validate_cilium.py).

Checks:
1. No empty files anywhere under deploy/cilium/.
2. Every .yaml/.yml parses (pyyaml); Grafana dashboard parses as JSON.
3. Every CiliumNetworkPolicy / CiliumClusterwideNetworkPolicy has both an
   endpointSelector AND at least one rule list (ingress/egress/
   ingressDeny/egressDeny).
4. Every TracingPolicy / TracingPolicyNamespaced has at least one probe
   list (kprobes/tracepoints).
5. Policies reference only components that exist in the platform inventory
   (infra/helm/sos-platform/values.yaml modules + known platform/data-plane
   components) or the kube-dns system label.
"""
import json
import re
from pathlib import Path

import pytest
import yaml

CILIUM_DIR = Path(__file__).resolve().parent
REPO_ROOT = CILIUM_DIR.parents[1]
HELM_VALUES = REPO_ROOT / "infra" / "helm" / "sos-platform" / "values.yaml"

POLICY_KINDS = {"CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy"}
TRACING_KINDS = {"TracingPolicy", "TracingPolicyNamespaced"}
RULE_KEYS = ("ingress", "egress", "ingressDeny", "egressDeny")


def _kebab(camel: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", camel).lower()


def _allowed_components() -> set[str]:
    values = yaml.safe_load(HELM_VALUES.read_text())
    comps = {_kebab(k) for k in values.get("modules", {})}
    # Top-level platform components rendered by other templates
    if values.get("apisix", {}).get("enabled"):
        comps.add("api-gateway")
    if values.get("keycloak", {}).get("enabled"):
        comps.add("keycloak")
    if values.get("tigerbeetle", {}).get("enabled"):
        comps.add("tigerbeetle")
    # Data-plane components referenced by values (postgresHost, keda.kafka)
    # and the shared local/prod inventory in deploy/docker-compose.yml.
    comps |= {"postgres", "redis", "kafka", "prometheus", "grafana", "keda"}
    return comps


ALLOWED_COMPONENTS = _allowed_components()

YAML_FILES = sorted(p for p in CILIUM_DIR.rglob("*") if p.suffix in (".yaml", ".yml"))
ALL_FILES = sorted(p for p in CILIUM_DIR.rglob("*") if p.is_file() and "__pycache__" not in str(p))


def _load_docs(path: Path):
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _component_refs(obj, acc):
    """Collect app.kubernetes.io/component values from a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "app.kubernetes.io/component":
                acc.add(v)
            else:
                _component_refs(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _component_refs(item, acc)


def test_no_empty_files():
    empty = [str(p) for p in ALL_FILES if p.stat().st_size == 0]
    assert not empty, f"empty files under deploy/cilium: {empty}"


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_yaml_parses(path):
    docs = _load_docs(path)
    assert docs, f"{path} contains no YAML documents"


def test_grafana_dashboard_parses():
    dash = json.loads((CILIUM_DIR / "observability" / "grafana-hubble-dashboard.json").read_text())
    assert dash.get("panels"), "dashboard has no panels"
    assert dash.get("uid"), "dashboard missing uid"


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_policies_have_selector_and_rules(path):
    for doc in _load_docs(path):
        kind = doc.get("kind")
        if kind not in POLICY_KINDS | TRACING_KINDS:
            continue
        spec = doc.get("spec") or {}
        name = doc.get("metadata", {}).get("name")
        if kind in POLICY_KINDS:
            assert "endpointSelector" in spec, f"{name}: missing endpointSelector"
            rules = [k for k in RULE_KEYS if spec.get(k)]
            assert rules, f"{name}: no ingress/egress rules"
        else:
            probes = [k for k in ("kprobes", "tracepoints", "uprobes") if spec.get(k)]
            assert probes, f"{name}: no probes defined"


@pytest.mark.parametrize("path", YAML_FILES, ids=lambda p: p.name)
def test_policies_reference_existing_services(path):
    for doc in _load_docs(path):
        if doc.get("kind") not in POLICY_KINDS | TRACING_KINDS:
            continue
        refs: set[str] = set()
        _component_refs(doc.get("spec") or {}, refs)
        unknown = refs - ALLOWED_COMPONENTS
        assert not unknown, (
            f"{doc['metadata'].get('name')}: unknown components {sorted(unknown)} "
            f"(not in helm values modules/platform inventory)"
        )
