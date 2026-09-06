#!/usr/bin/env python3
"""Static validator for infra/ manifests.

Yaml-parses every Kubernetes/Helm/ArgoCD manifest under infra/ and asserts
the tenancy invariants from docs/architecture/06-tenancy-security.md:

  1. Every state overlay carries at least one NetworkPolicy for its namespace
     (cross-state egress blocking).
  2. Every state namespace carries a ResourceQuota.
  3. The dedicated tier (Lagos) contains no references to shared data-plane
     resources (shared Postgres cluster, shared tier naming).
  4. Every state tenant has an ArgoCD Application with an automated sync
     policy targeting the correct sos-<state> namespace.
  5. Every Terraform module ships main.tf / variables.tf / outputs.tf, has
     balanced braces, and contains no embedded credentials.

Usage: python3 infra/tests/validate_infra.py  (exit 0 = pass)
Requires: pyyaml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"

STATES = {
    "lagos": "dedicated-tier",
    "ogun": "hybrid-tier",
    "osun": "shared-tier",
    "benue": "shared-tier",
    "nasarawa": "shared-tier",
    "taraba": "shared-tier",
}

# Patterns that must never appear in a dedicated-tier overlay (shared
# data-plane references are a tenancy-isolation breach).
FORBIDDEN_IN_DEDICATED = ["sos-postgres-shared", "shared-tier3", "sos-data"]

FAILURES: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"  ok: {msg}")


def load_yaml_docs(path: Path) -> list[dict]:
    """Parse a manifest file into YAML documents. Helm templates are parsed
    after substituting Go-template expressions with inert placeholders.
    Returns [] for empty documents / files with only templating left over."""
    text = path.read_text()
    if "{{" in text:  # helm template — neutralize Go template syntax
        # Drop lines consisting solely of a template expression
        # (control structures, `include ... | indent N` blocks, toYaml calls).
        text = re.sub(r"^\s*\{\{-?[^}]*\}\}\s*$", "", text, flags=re.MULTILINE)
        # Inline expressions become an inert scalar token.
        text = re.sub(r"\{\{-?[^}]*\}\}", "__tpl__", text)
    docs = []
    for doc in yaml.safe_load_all(text):
        if doc is None:
            continue
        if not isinstance(doc, dict):
            fail(f"{path.relative_to(REPO_ROOT)}: document is not a mapping")
            continue
        docs.append(doc)
    return docs


def check_k8s_overlays() -> None:
    print("== k8s overlays ==")
    for state, overlay in STATES.items():
        overlay_dir = INFRA / "k8s" / "overlays" / overlay
        state_file = overlay_dir / "states" / f"{state}.yaml"
        if not state_file.exists():
            fail(f"{state}: missing manifest {state_file.relative_to(REPO_ROOT)}")
            continue
        docs = load_yaml_docs(state_file)
        kinds = [d.get("kind") for d in docs]
        ns = f"sos-{state}"

        netpols = [
            d for d in docs
            if d.get("kind") == "NetworkPolicy"
            and d.get("metadata", {}).get("namespace") == ns
        ]
        if not netpols:
            fail(f"{state}: no NetworkPolicy in namespace {ns}")
        else:
            for np in netpols:
                spec = np.get("spec", {})
                if "Egress" not in spec.get("policyTypes", []):
                    fail(f"{state}: NetworkPolicy {np['metadata']['name']} lacks Egress policyType")
            ok(f"{state}: {len(netpols)} NetworkPolicy(s) with egress control")

        quotas = [
            d for d in docs
            if d.get("kind") == "ResourceQuota"
            and d.get("metadata", {}).get("namespace") == ns
        ]
        if not quotas or not quotas[0].get("spec", {}).get("hard"):
            fail(f"{state}: no ResourceQuota with hard limits in namespace {ns}")
        else:
            ok(f"{state}: ResourceQuota present")

        namespaces = [
            d for d in docs
            if d.get("kind") == "Namespace" and d.get("metadata", {}).get("name") == ns
        ]
        if not namespaces:
            fail(f"{state}: no Namespace manifest for {ns}")
        else:
            labels = namespaces[0]["metadata"].get("labels", {})
            if labels.get("sos.gov.ng/tenant-state") != state:
                fail(f"{state}: namespace missing tenant-state label")

        if "NetworkPolicy" not in kinds or "ResourceQuota" not in kinds:
            fail(f"{state}: overlay incomplete (kinds found: {sorted(set(map(str, kinds)))})")

    # kustomization files must parse and reference the state manifests
    for overlay in {o for o in STATES.values()}:
        kpath = INFRA / "k8s" / "overlays" / overlay / "kustomization.yaml"
        try:
            kdocs = load_yaml_docs(kpath)
        except yaml.YAMLError as exc:
            fail(f"{kpath.relative_to(REPO_ROOT)}: invalid YAML: {exc}")
            continue
        if not kdocs or kdocs[0].get("kind") != "Kustomization":
            fail(f"{overlay}: kustomization.yaml missing or invalid")
        else:
            ok(f"{overlay}: kustomization valid")


def check_dedicated_isolation() -> None:
    print("== dedicated-tier isolation ==")
    dedicated = INFRA / "k8s" / "overlays" / "dedicated-tier"
    violations = []
    for path in sorted(dedicated.rglob("*.yaml")):
        text = path.read_text()
        for pattern in FORBIDDEN_IN_DEDICATED:
            if pattern in text:
                violations.append(f"{path.relative_to(REPO_ROOT)} references {pattern!r}")
    # Lagos ArgoCD app must also not point at shared data plane
    lagos_app = INFRA / "gitops" / "applications" / "sos-lagos.yaml"
    if lagos_app.exists():
        text = lagos_app.read_text()
        for pattern in FORBIDDEN_IN_DEDICATED:
            if pattern in text:
                violations.append(f"{lagos_app.relative_to(REPO_ROOT)} references {pattern!r}")
    if violations:
        for v in violations:
            fail(v)
    else:
        ok("dedicated tier has no shared data-plane references")


def check_helm_chart() -> None:
    print("== helm chart ==")
    chart = INFRA / "helm" / "sos-platform"
    for required in ("Chart.yaml", "values.yaml"):
        path = chart / required
        if not path.exists():
            fail(f"helm: missing {required}")
            continue
        try:
            docs = load_yaml_docs(path)
        except yaml.YAMLError as exc:
            fail(f"helm {required}: invalid YAML: {exc}")
            continue
        if not docs:
            fail(f"helm {required}: empty document")
    templates = sorted((chart / "templates").glob("*.yaml"))
    if not templates:
        fail("helm: no templates found")
    for tpl in templates:
        try:
            docs = load_yaml_docs(tpl)
        except yaml.YAMLError as exc:
            fail(f"helm template {tpl.name}: does not parse after template substitution: {exc}")
            continue
        for doc in docs:
            if not doc.get("kind"):
                fail(f"helm template {tpl.name}: document without kind")
        ok(f"helm template {tpl.name}: parses ({len(docs)} doc(s))")
    # scaffolding components required by the mission
    names = " ".join(t.name for t in templates)
    for component in ("apisix", "keycloak", "mod-rev-core", "kubecost", "keda"):
        if component not in names:
            fail(f"helm: no template covering {component}")


def check_gitops() -> None:
    print("== gitops ==")
    apps_dir = INFRA / "gitops" / "applications"
    for state, overlay in STATES.items():
        app_file = apps_dir / f"sos-{state}.yaml"
        if not app_file.exists():
            fail(f"gitops: missing Application for sos-{state}")
            continue
        try:
            docs = load_yaml_docs(app_file)
        except yaml.YAMLError as exc:
            fail(f"gitops sos-{state}: invalid YAML: {exc}")
            continue
        app = docs[0] if docs else {}
        if app.get("kind") != "Application":
            fail(f"gitops sos-{state}: kind is not Application")
            continue
        spec = app.get("spec", {})
        dest_ns = spec.get("destination", {}).get("namespace")
        if dest_ns != f"sos-{state}":
            fail(f"gitops sos-{state}: destination namespace {dest_ns!r} != sos-{state}")
        sync = spec.get("syncPolicy", {}).get("automated", {})
        if not (sync.get("prune") and sync.get("selfHeal")):
            fail(f"gitops sos-{state}: syncPolicy.automatic requires prune+selfHeal")
        paths = [s.get("path", "") for s in spec.get("sources", [])]
        if f"infra/k8s/overlays/{overlay}" not in paths:
            fail(f"gitops sos-{state}: does not reference overlay {overlay}")
        else:
            ok(f"gitops sos-{state}: valid (overlay={overlay}, ns=sos-{state})")
    project = INFRA / "gitops" / "projects" / "sos-states-project.yaml"
    try:
        pdocs = load_yaml_docs(project)
    except yaml.YAMLError as exc:
        fail(f"gitops project: invalid YAML: {exc}")
        return
    if not pdocs or pdocs[0].get("kind") != "AppProject":
        fail("gitops: AppProject declaration missing/invalid")
    else:
        ok("gitops AppProject valid")


def check_terraform() -> None:
    print("== terraform ==")
    secret_patterns = [r"access_key\s*=\s*\"", r"secret_key\s*=\s*\"", r"password\s*=\s*\""]
    tf_root = INFRA / "terraform"
    modules = sorted(p for p in (tf_root / "modules").iterdir() if p.is_dir())
    if len(modules) < 3:
        fail(f"terraform: expected >=3 modules, found {len(modules)}")
    for mod in modules:
        for required in ("main.tf", "variables.tf", "outputs.tf"):
            if not (mod / required).exists():
                fail(f"terraform module {mod.name}: missing {required}")
        for tf in sorted(mod.glob("*.tf")):
            text = tf.read_text()
            if text.count("{") != text.count("}"):
                fail(f"terraform {tf.relative_to(REPO_ROOT)}: unbalanced braces")
            for pat in secret_patterns:
                if re.search(pat, text):
                    fail(f"terraform {tf.relative_to(REPO_ROOT)}: possible embedded credential")
        ok(f"terraform module {mod.name}: structure ok")
    envs = sorted(p for p in (tf_root / "envs").iterdir() if p.is_dir())
    expected_envs = {"tier1-dedicated", "tier2-hybrid", "tier3-shared"}
    if {e.name for e in envs} != expected_envs:
        fail(f"terraform envs: expected {sorted(expected_envs)}, found {[e.name for e in envs]}")
    for env in envs:
        main = env / "main.tf"
        if not main.exists():
            fail(f"terraform env {env.name}: missing main.tf")
            continue
        text = main.read_text()
        for mod_name in ("sovereign-cluster", "object-storage", "kms-keyring"):
            if mod_name not in text:
                fail(f"terraform env {env.name}: does not wire module {mod_name}")
        ok(f"terraform env {env.name}: wiring ok")


def main() -> int:
    check_k8s_overlays()
    check_helm_chart()
    check_terraform()
    check_gitops()
    check_dedicated_isolation()

    print()
    if FAILURES:
        print(f"VALIDATION FAILED: {len(FAILURES)} problem(s)")
        return 1
    print("VALIDATION PASSED: all infra invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
