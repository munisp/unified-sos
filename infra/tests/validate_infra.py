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
        # Strip {{/* ... */}} block comments (may span multiple lines).
        text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.DOTALL)
        # Drop lines consisting solely of a template expression
        # (control structures, `include ... | indent N` blocks, toYaml calls).
        text = re.sub(r"^\s*\{\{-?.*?-?\}\}\s*$", "", text, flags=re.MULTILINE)
        # Inline expressions become an inert scalar token.
        text = re.sub(r"\{\{-?.*?-?\}\}", "__tpl__", text)
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
    for component in ("apisix", "keycloak", "modules", "kubecost", "keda"):
        if component not in names:
            fail(f"helm: no template covering {component}")


def kebab_to_camel(name: str) -> str:
    """mod-agri-waybill -> modAgriWaybill; control-plane -> controlPlane."""
    parts = name.split("-")
    return parts[0] + "".join(p.title() for p in parts[1:])


def check_helm_modules_rendered() -> None:
    print("== helm modules ==")
    chart = INFRA / "helm" / "sos-platform"
    values_file = chart / "values.yaml"
    try:
        values = load_yaml_docs(values_file)[0]
    except (yaml.YAMLError, IndexError) as exc:
        fail(f"helm modules: cannot read values.yaml: {exc}")
        return
    modules = values.get("modules")
    if not isinstance(modules, dict):
        fail("helm modules: values.yaml has no 'modules' map")
        return

    # Every module service under services/ must have a values entry.
    services_dir = REPO_ROOT / "services"
    service_dirs = sorted(
        p.name for p in services_dir.iterdir()
        if p.is_dir() and (p.name.startswith("mod-") or p.name in ("control-plane", "lakehouse"))
    )
    for svc in service_dirs:
        key = kebab_to_camel(svc)
        entry = modules.get(key)
        if entry is None:
            fail(f"helm modules: no values.modules.{key} for services/{svc}")
            continue
        for field in ("enabled", "replicaCount", "image", "service", "env", "resources"):
            if field not in entry:
                fail(f"helm modules: values.modules.{key} missing '{field}'")
        if entry.get("image", {}).get("repository") != f"ghcr.io/munisp/{svc}":
            fail(f"helm modules: values.modules.{key} image.repository != ghcr.io/munisp/{svc}")
        port = entry.get("service", {}).get("port")
        if not isinstance(port, int):
            fail(f"helm modules: values.modules.{key} service.port missing/not int")
    else:
        ok(f"helm modules: all {len(service_dirs)} services have values.modules entries")

    # Schema must lock the modules map down to the known service keys.
    schema_file = chart / "values.schema.json"
    if not schema_file.exists():
        fail("helm modules: values.schema.json missing")
    else:
        try:
            import json
            schema = json.loads(schema_file.read_text())
            mod_schema = schema.get("properties", {}).get("modules", {})
            if mod_schema.get("additionalProperties") is not False:
                fail("helm modules: values.schema.json modules.additionalProperties must be false")
            declared = set(mod_schema.get("properties", {}))
            expected = {kebab_to_camel(s) for s in service_dirs}
            if declared != expected:
                fail(f"helm modules: schema module keys {sorted(declared)} != services {sorted(expected)}")
            else:
                ok("helm modules: values.schema.json modules map is closed and complete")
        except (ValueError, AttributeError) as exc:
            fail(f"helm modules: values.schema.json invalid: {exc}")

    # Named template + range template must exist.
    tpl = chart / "templates" / "_module-deployment.tpl"
    modules_tpl = chart / "templates" / "modules.yaml"
    if not tpl.exists() or 'define "sos-platform.moduleDeployment"' not in tpl.read_text():
        fail("helm modules: _module-deployment.tpl missing sos-platform.moduleDeployment")
    elif not modules_tpl.exists():
        fail("helm modules: templates/modules.yaml missing")
    else:
        # Parse both via the neutralization approach (also used when helm
        # is unavailable) to prove they contain no stray YAML errors.
        try:
            load_yaml_docs(modules_tpl)
            load_yaml_docs(tpl)
            ok("helm modules: modules.yaml + _module-deployment.tpl parse after template substitution")
        except yaml.YAMLError as exc:
            fail(f"helm modules: module templates do not parse: {exc}")

    # If helm is available, render for a couple of tenantStateId values and
    # YAML-parse the output; otherwise note the skip.
    import shutil
    import subprocess
    helm = shutil.which("helm")
    if not helm:
        print("  note: helm binary not on PATH — skipping helm template render check")
        return
    for state in ("osun", "lagos"):
        proc = subprocess.run(
            [helm, "template", f"sos-{state}", str(chart), "--set", f"global.tenantStateId={state}"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            fail(f"helm modules: helm template failed for tenantStateId={state}: {proc.stderr.strip()}")
            continue
        try:
            docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
        except yaml.YAMLError as exc:
            fail(f"helm modules: rendered output for {state} is not valid YAML: {exc}")
            continue
        kinds = [d.get("kind") for d in docs if isinstance(d, dict)]
        enabled = [k for k, m in modules.items() if isinstance(m, dict) and m.get("enabled")]
        deployments = [d for d in docs if isinstance(d, dict) and d.get("kind") == "Deployment"]
        rendered_components = {
            d.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
            for d in deployments
        }
        for key in enabled:
            kebab = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", key).lower()
            if kebab not in rendered_components:
                fail(f"helm modules: enabled module {key} not rendered for tenantStateId={state}")
        ok(f"helm modules: helm template renders {len(docs)} docs for tenantStateId={state} ({sorted(set(map(str, kinds)))})")


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


def check_realm_drift() -> None:
    """Per-state Keycloak realms must match the renderer output, and the
    copies bundled into the Helm chart (for .Files.Get) must match the
    canonical render in deploy/keycloak/realms/."""
    print("== keycloak realm drift ==")
    import subprocess

    renderer = REPO_ROOT / "deploy" / "keycloak" / "render_realms.py"
    result = subprocess.run(
        [sys.executable, str(renderer), "--check"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(f"keycloak realms drifted — re-run {renderer.relative_to(REPO_ROOT)}\n{result.stderr.strip()}")
    else:
        ok("render_realms.py --check passed (6 realms up to date)")
    canonical = REPO_ROOT / "deploy" / "keycloak" / "realms"
    bundled = INFRA / "helm" / "sos-platform" / "realms"
    mismatches = []
    for realm_file in sorted(canonical.glob("realm-sos-*.json")):
        chart_copy = bundled / realm_file.name
        if not chart_copy.exists():
            mismatches.append(f"helm chart missing bundled realm {chart_copy.relative_to(REPO_ROOT)}")
        elif chart_copy.read_text() != realm_file.read_text():
            mismatches.append(f"helm bundled realm {chart_copy.relative_to(REPO_ROOT)} out of sync with {realm_file.relative_to(REPO_ROOT)}")
    if mismatches:
        for m in mismatches:
            fail(m)
    else:
        ok("helm chart bundled realms match canonical render")
def check_tigerbeetle() -> None:
    """TigerBeetle ledger-plane invariants (ADR-002 / Clause 22.2):

      1. Replica count is odd and >= 3 (VSR quorum) everywhere it is set.
      2. Ledger state lives on PVCs (volumeClaimTemplates) — hostPath is
         forbidden anywhere under infra/.
      3. The terraform module wires cluster/replica args and PVCs.
    """
    print("== tigerbeetle ledger plane ==")

    # 1+2: helm chart
    tpl = INFRA / "helm" / "sos-platform" / "templates" / "tigerbeetle-statefulset.yaml"
    if not tpl.exists():
        fail("tigerbeetle: missing helm template tigerbeetle-statefulset.yaml")
    else:
        text = tpl.read_text()
        if "volumeClaimTemplates" not in text:
            fail("tigerbeetle helm: StatefulSet has no volumeClaimTemplates (PVC required)")
        if "hostPath:" in text:
            fail("tigerbeetle helm: hostPath reference is forbidden")
        docs = load_yaml_docs(tpl)
        kinds = {d.get("kind") for d in docs}
        if "StatefulSet" not in kinds:
            fail("tigerbeetle helm: no StatefulSet document")
        values = load_yaml_docs(INFRA / "helm" / "sos-platform" / "values.yaml")
        replicas = (values[0].get("tigerbeetle", {}) or {}).get("replicaCount") if values else None
        if not isinstance(replicas, int) or replicas < 3 or replicas % 2 == 0:
            fail(f"tigerbeetle helm: replicaCount {replicas!r} must be odd and >= 3")
        else:
            ok(f"tigerbeetle helm: {replicas} replicas, PVC-backed, no hostPath")

    # 3: terraform module
    mod = INFRA / "terraform" / "modules" / "tigerbeetle"
    if not mod.is_dir():
        fail("tigerbeetle: missing terraform module infra/terraform/modules/tigerbeetle")
    else:
        main_tf = (mod / "main.tf").read_text()
        var_tf = (mod / "variables.tf").read_text()
        for needle in ("kubernetes_stateful_set", "volume_claim_template",
                       "--cluster=", "--replica", "--addresses"):
            if needle not in main_tf:
                fail(f"tigerbeetle terraform: main.tf missing {needle!r}")
        if "host_path" in main_tf or "hostPath:" in main_tf:
            fail("tigerbeetle terraform: hostPath is forbidden")
        if "replica_count % 2 == 1" not in var_tf or ">= 3" not in var_tf:
            fail("tigerbeetle terraform: replica_count must validate odd >= 3")
        ok("tigerbeetle terraform: module wiring ok")

    # 4: per-tier overlay wiring
    for state, overlay in STATES.items():
        state_file = INFRA / "k8s" / "overlays" / overlay / "states" / f"{state}.yaml"
        if not state_file.exists():
            continue
        docs = load_yaml_docs(state_file)
        ledger_cm = [
            d for d in docs
            if d.get("kind") == "ConfigMap"
            and d.get("metadata", {}).get("name") == f"sos-{state}-ledger"
        ]
        if not ledger_cm:
            fail(f"{state}: overlay missing sos-{state}-ledger ConfigMap (TB wiring)")
            continue
        data = ledger_cm[0].get("data", {})
        if not data.get("TB_ADDRESSES") or data.get("TB_CLUSTER_ID") != "1":
            fail(f"{state}: sos-{state}-ledger ConfigMap must set TB_ADDRESSES and TB_CLUSTER_ID=1")
        else:
            ok(f"{state}: ledger wiring ok ({data['TB_ADDRESSES']})")

    # Global hostPath ban across infra manifests.
    for path in sorted(INFRA.rglob("*.yaml")):
        if "hostPath:" in path.read_text():
            fail(f"{path.relative_to(REPO_ROOT)}: hostPath reference is forbidden")


def check_openappsec_waf() -> None:
    """OpenAppSec WAF policy pack must parse, keep a default-deny posture
    aligned with the Cilium matrix (infra/k8s/base/networkpolicy-default-deny.yaml),
    and ship a renderable Helm chart under infra/helm/openappsec."""
    print("== openappsec WAF policy ==")
    pack = REPO_ROOT / "contracts" / "policy-packs" / "examples" / "openappsec-waf-default-deny.yaml"
    if not pack.exists():
        fail(f"openappsec: missing policy pack {pack.relative_to(REPO_ROOT)}")
        return
    try:
        docs = load_yaml_docs(pack)
    except yaml.YAMLError as exc:
        fail(f"openappsec policy pack: invalid YAML: {exc}")
        return
    policy = docs[0] if docs else {}
    if policy.get("kind") != "Policy":
        fail("openappsec: policy pack kind != Policy")
    spec = policy.get("spec", {})
    if spec.get("default") != "deny":
        fail("openappsec: spec.default must be 'deny' (default-deny posture)")
    rules = spec.get("rules") or []
    terminal = rules[-1] if rules else {}
    if not (terminal.get("action") == "deny" and terminal.get("match") == "true"):
        fail("openappsec: last rule must be a catch-all deny (match: 'true', action: deny)")
    if not any(r.get("action") in ("accept", "detect") for r in rules[:-1]):
        fail("openappsec: no explicit allow/inspect rules before the catch-all deny")
    # alignment with the Cilium default-deny matrix
    cilium = INFRA / "k8s" / "base" / "networkpolicy-default-deny.yaml"
    if not cilium.exists():
        fail("openappsec: Cilium default-deny matrix file missing in infra/k8s/base")
    elif "cilium" not in str(policy.get("metadata", {}).get("labels", {})).lower():
        fail("openappsec: policy pack must label its alignment with the Cilium matrix")
    else:
        ok("openappsec: default-deny policy pack aligned with Cilium matrix")

    chart = INFRA / "helm" / "openappsec"
    for required in ("Chart.yaml", "values.yaml"):
        if not (chart / required).exists():
            fail(f"openappsec chart: missing {required}")
    templates = sorted((chart / "templates").glob("*.yaml"))
    if not templates:
        fail("openappsec chart: no templates found")
    for tpl in templates:
        try:
            docs = load_yaml_docs(tpl)
        except yaml.YAMLError as exc:
            fail(f"openappsec template {tpl.name}: does not parse: {exc}")
            continue
        for doc in docs:
            if not doc.get("kind"):
                fail(f"openappsec template {tpl.name}: document without kind")
        ok(f"openappsec template {tpl.name}: parses ({len(docs)} doc(s))")
    # The chart must actually render the default-deny policy.
    policy_cm = chart / "templates" / "configmap-policy.yaml"
    if policy_cm.exists():
        text = policy_cm.read_text()
        if "deny" not in text or "policy" not in text.lower():
            fail("openappsec chart: configmap-policy.yaml does not embed the deny policy")
    else:
        fail("openappsec chart: missing templates/configmap-policy.yaml")


def main() -> int:
    check_k8s_overlays()
    check_realm_drift()
    check_openappsec_waf()
    check_helm_chart()
    check_helm_modules_rendered()
    check_terraform()
    check_gitops()
    check_dedicated_isolation()
    check_tigerbeetle()

    print()
    if FAILURES:
        print(f"VALIDATION FAILED: {len(FAILURES)} problem(s)")
        return 1
    print("VALIDATION PASSED: all infra invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
