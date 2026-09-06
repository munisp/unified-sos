"""GitOps bundle generation for tenant provisioning (WP-01 / EP-CP-01).

Generates the tenant infrastructure bundle — Kubernetes namespace, Postgres
schema with Row-Level Security, Keycloak realm, S3 bucket, and KMS keyring
manifests — into a target directory. Output is deterministic (stable ordering,
no timestamps) so repeated runs are idempotent.

In production these files are committed to ``infra/gitops`` and reconciled by
ArgoCD (see tools/README.md design rules). The bundle itself is pure metadata:
the control plane holds ZERO citizen PII.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .states import STATE_TENANT_IDS, TIERS

# Resource names follow a strict convention so they are predictable everywhere.
def k8s_namespace(state: str, tier: str) -> str:
    return f"sos-{state}-{tier}"


def postgres_schema(state: str) -> str:
    return f"tenant_{state}"


def keycloak_realm(state: str) -> str:
    return f"sos-{state}"


def s3_bucket(state: str, tier: str) -> str:
    return f"sos-{state}-{tier}-data"


def kms_keyring(state: str) -> str:
    return f"sos-{state}-keyring"


@dataclass(frozen=True)
class GitopsBundle:
    """Result of rendering a tenant GitOps bundle."""

    state: str
    tier: str
    out_dir: Path
    files: dict[str, str] = field(default_factory=dict)  # relative path -> content

    @property
    def manifest(self) -> dict[str, str]:
        return {
            "k8s_namespace": k8s_namespace(self.state, self.tier),
            "postgres_schema": postgres_schema(self.state),
            "keycloak_realm": keycloak_realm(self.state),
            "s3_bucket": s3_bucket(self.state, self.tier),
            "kms_keyring": kms_keyring(self.state),
        }


def _render_namespace(state: str, tier: str) -> str:
    ns = k8s_namespace(state, tier)
    labels = {
        "sos.gov.ng/tenant": state,
        "sos.gov.ng/tier": tier,
        "sos.gov.ng/part-of": "state-operating-system",
        "pod-security.kubernetes.io/enforce": "restricted",
    }
    label_yaml = "\n".join(f"    {k}: {v}" for k, v in labels.items())
    return (
        "apiVersion: v1\n"
        "kind: Namespace\n"
        "metadata:\n"
        f"  name: {ns}\n"
        "  labels:\n"
        f"{label_yaml}\n"
        "---\n"
        "# Default-deny: zero cross-tenant access under Cilium (WP-01 acceptance)\n"
        "apiVersion: cilium.io/v2\n"
        "kind: CiliumNetworkPolicy\n"
        "metadata:\n"
        f"  name: tenant-isolation-{state}\n"
        f"  namespace: {ns}\n"
        "spec:\n"
        "  endpointSelector: {}\n"
        "  ingress:\n"
        "    - fromEndpoints:\n"
        "        - matchLabels:\n"
        f"            sos.gov.ng/tenant: {state}\n"
        "  egress:\n"
        "    - toEndpoints:\n"
        "        - matchLabels:\n"
        f"            sos.gov.ng/tenant: {state}\n"
    )


def _render_postgres_rls(state: str) -> str:
    schema = postgres_schema(state)
    role = f"sos_tenant_{state}"
    return f"""-- Postgres schema bootstrap with Row-Level Security for tenant '{state}'.
-- Tenant isolation is non-negotiable (CONTRIBUTING.md rule 4).
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE ROLE {role} NOLOGIN;

-- Every tenant table must carry tenant_state_id and enable RLS.
-- Template applied by the SOS Tenant Operator to each data-plane table:
--
--   ALTER TABLE {schema}.<table> ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY tenant_isolation ON {schema}.<table>
--     USING (tenant_state_id = current_setting('sos.tenant_state_id'));

CREATE POLICY tenant_isolation_template ON {schema}.tenant_registry
  USING (tenant_state_id = '{state}');
GRANT USAGE ON SCHEMA {schema} TO {role};
"""


def _render_keycloak_realm(state: str) -> str:
    realm = {
        "realm": keycloak_realm(state),
        "enabled": True,
        "displayName": f"SOS — {state.title()} State Tenant",
        "attributes": {
            "sos.tenant_state_id": state,
            "sos.data_residency": "ng",
        },
        "sslRequired": "external",
        "bruteForceProtected": True,
        "clients": [
            {
                "clientId": "sos-control-plane",
                "description": "Control-plane metadata plane only — zero citizen PII",
                "serviceAccountsEnabled": True,
                "publicClient": False,
            }
        ],
    }
    return json.dumps(realm, indent=2, sort_keys=True) + "\n"


def _render_storage(state: str, tier: str) -> str:
    bucket = s3_bucket(state, tier)
    keyring = kms_keyring(state)
    manifest = {
        "apiVersion": "sos.gov.ng/v1alpha1",
        "kind": "TenantStorage",
        "metadata": {"name": f"{state}-storage", "labels": {"sos.gov.ng/tenant": state}},
        "spec": {
            "s3_bucket": {
                "name": bucket,
                "region": "ng-lagos-1",
                "versioning": True,
                "encryption": {"algorithm": "aws:kms", "keyring": keyring},
                "publicAccessBlock": True,
            },
            "kms_keyring": {
                "name": keyring,
                "rotation_days": 90,
                "dedicated": tier == "dedicated",
            },
        },
    }
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def render_bundle(state: str, tier: str) -> dict[str, str]:
    """Render all bundle files as {relative_path: content}. Deterministic."""
    if state not in STATE_TENANT_IDS:
        raise ValueError(
            f"unknown state '{state}'; valid: {', '.join(sorted(STATE_TENANT_IDS))}"
        )
    if tier not in TIERS:
        raise ValueError(f"unknown tier '{tier}'; valid: {', '.join(TIERS)}")
    return {
        "k8s/namespace.yaml": _render_namespace(state, tier),
        "postgres/schema-rls.sql": _render_postgres_rls(state),
        "keycloak/realm.json": _render_keycloak_realm(state),
        "storage/s3-kms.json": _render_storage(state, tier),
    }


def write_bundle(state: str, tier: str, out_dir: Path) -> GitopsBundle:
    """Render and write the bundle. Idempotent: identical content is left in place."""
    files = render_bundle(state, tier)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in sorted(files.items()):
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Idempotent write: skip when content already matches.
        if path.exists() and path.read_text() == content:
            continue
        path.write_text(content)
    return GitopsBundle(state=state, tier=tier, out_dir=out_dir, files=files)
