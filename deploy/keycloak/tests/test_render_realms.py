"""Tests for the per-state Keycloak realm renderer (P2 Workstream B)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

KEYCLOAK_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = KEYCLOAK_DIR.parents[1]
sys.path.insert(0, str(KEYCLOAK_DIR))

import render_realms  # noqa: E402

STATES = ["lagos", "ogun", "osun", "benue", "nasarawa", "taraba"]
ROLES = {"sos-admin", "sos-revenue-officer", "sos-auditor-readonly", "sos-citizen"}


def load_realm(state: str) -> dict:
    return json.loads(render_realms.realm_path(state).read_text())


def test_six_realms_render():
    for state in STATES:
        realm = load_realm(state)
        assert realm["realm"] == f"sos-{state}"
        assert realm["enabled"] is True


def test_all_state_ids_present():
    rendered = {load_realm(s)["realm"] for s in STATES}
    assert rendered == {f"sos-{s}" for s in STATES}


def test_realm_shape_matches_dev_realm():
    dev = json.loads((KEYCLOAK_DIR / "realm-sos-dev.json").read_text())
    for state in STATES:
        realm = load_realm(state)
        # same top-level shape minus users (state realms never carry users)
        assert set(realm) == set(dev) - {"users"}
        assert {r["name"] for r in dev["roles"]["realm"]} <= {
            r["name"] for r in realm["roles"]["realm"]
        }
        assert {c["clientId"] for c in dev["clients"]} <= {
            c["clientId"] for c in realm["clients"]
        }


def test_roles_and_clients():
    for state in STATES:
        realm = load_realm(state)
        assert {r["name"] for r in realm["roles"]["realm"]} == ROLES
        clients = {c["clientId"]: c for c in realm["clients"]}
        assert clients["sos-services"]["serviceAccountsEnabled"] is True
        assert clients["sos-services"]["publicClient"] is False
        portal = clients["sos-citizen-portal"]
        assert portal["publicClient"] is True
        assert portal["attributes"]["pkce.code.challenge.method"] == "S256"
        assert realm["sslRequired"] == "external"


def test_no_users_and_no_secrets():
    for state in STATES:
        realm = load_realm(state)
        assert "users" not in realm
        text = render_realms.realm_path(state).read_text()
        assert '"secret"' not in text
        assert "not-a-secret" not in text
        for client in realm["clients"]:
            assert "secret" not in client


def test_check_mode_idempotent():
    result = subprocess.run(
        [sys.executable, str(KEYCLOAK_DIR / "render_realms.py"), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_reruns_byte_identical(tmp_path):
    before = {s: render_realms.realm_path(s).read_bytes() for s in STATES}
    subprocess.run(
        [sys.executable, str(KEYCLOAK_DIR / "render_realms.py")], check=True,
        capture_output=True,
    )
    for state in STATES:
        assert render_realms.realm_path(state).read_bytes() == before[state]


def test_helm_chart_bundled_realms_in_sync():
    bundled = REPO_ROOT / "infra" / "helm" / "sos-platform" / "realms"
    for state in STATES:
        canonical = render_realms.realm_path(state).read_text()
        assert (bundled / f"realm-sos-{state}.json").read_text() == canonical


def test_display_names_from_policy_packs():
    for state in STATES:
        realm = load_realm(state)
        assert realm["displayName"] == render_realms.state_metadata(state)["display_name"]


@pytest.mark.parametrize("state", STATES)
def test_tier_matches_modules_yaml(state):
    realm = load_realm(state)
    tier = render_realms.state_metadata(state)["tier"]
    assert tier in json.dumps(realm["roles"])
