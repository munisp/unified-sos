#!/usr/bin/env python3
"""Deterministic renderer for the six per-state SOS Keycloak realms.

Reads the shared template templates/realm-sos-state.json.tmpl and the state
metadata in config/states/<state>/ (policy-pack.json for the tenant id /
display name, modules.yaml for the tenancy tier) and emits one realm JSON
per state into deploy/keycloak/realms/.

Stdlib only. Output is canonical (sorted keys, 2-space indent, trailing
newline) so reruns are byte-identical.

Usage:
    python3 deploy/keycloak/render_realms.py          # render all realms
    python3 deploy/keycloak/render_realms.py --check  # fail on drift
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
TEMPLATE = HERE / "templates" / "realm-sos-state.json.tmpl"
REALMS_DIR = HERE / "realms"
STATES_DIR = REPO_ROOT / "config" / "states"

STATES = ["lagos", "ogun", "osun", "benue", "nasarawa", "taraba"]


def state_metadata(state: str) -> dict:
    """Pull display name and tier from config/states/<state>/."""
    pack = json.loads((STATES_DIR / state / "policy-pack.json").read_text())
    tenant_state_id = pack["tenant_state_id"]
    if tenant_state_id != state:
        raise ValueError(
            f"config/states/{state}/policy-pack.json: tenant_state_id "
            f"{tenant_state_id!r} != directory name {state!r}"
        )
    modules_text = (STATES_DIR / state / "modules.yaml").read_text()
    m = re.search(r"^tenancy_tier:\s*(\S+)", modules_text, flags=re.MULTILINE)
    tier = m.group(1) if m else "shared"
    return {
        "state_id": tenant_state_id,
        "display_name": f"SOS {tenant_state_id.replace('-', ' ').title()} State",
        "tier": tier,
    }


def render_realm(state: str) -> str:
    """Render one state's realm to canonical JSON text."""
    text = TEMPLATE.read_text()
    for key, value in state_metadata(state).items():
        text = text.replace("{{%s}}" % key, value)
    unreplaced = re.findall(r"\{\{[a-z_]+\}\}", text)
    if unreplaced:
        raise ValueError(f"unrendered template variables for {state}: {unreplaced}")
    realm = json.loads(text)
    realm.pop("_comment", None)
    return json.dumps(realm, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def realm_path(state: str) -> Path:
    return REALMS_DIR / f"realm-sos-{state}.json"


def render_all() -> int:
    REALMS_DIR.mkdir(parents=True, exist_ok=True)
    for state in STATES:
        path = realm_path(state)
        path.write_text(render_realm(state))
        print(f"rendered {path.relative_to(REPO_ROOT)}")
    return 0


def check_drift() -> int:
    """Exit 1 if any rendered realm differs from what is on disk."""
    drifted = []
    for state in STATES:
        path = realm_path(state)
        expected = render_realm(state)
        if not path.exists():
            drifted.append(f"{path.relative_to(REPO_ROOT)}: missing")
        elif path.read_text() != expected:
            drifted.append(f"{path.relative_to(REPO_ROOT)}: stale (re-run render_realms.py)")
    if drifted:
        for d in drifted:
            print(f"DRIFT: {d}", file=sys.stderr)
        return 1
    print(f"realm check ok: {len(STATES)} realms up to date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on realm drift")
    args = parser.parse_args()
    return check_drift() if args.check else render_all()


if __name__ == "__main__":
    sys.exit(main())
