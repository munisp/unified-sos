"""Tests for tenant whitelabel branding in the GitOps bundle + CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sosctl import gitops
from sosctl.cli import app

runner = CliRunner()


def _create(state: str, tier: str, tmp_path: Path):
    return runner.invoke(
        app,
        [
            "tenant", "create",
            f"--state={state}", f"--tier={tier}",
            f"--out-dir={tmp_path / 'gitops'}",
            f"--registry={tmp_path / 'reg' / 'tenants.json'}",
        ],
    )


def test_bundle_includes_branding_and_host_routing(tmp_path: Path) -> None:
    result = _create("lagos", "hybrid", tmp_path)
    assert result.exit_code == 0, result.output
    out = tmp_path / "gitops"
    branding = json.loads((out / "branding" / "branding.json").read_text())
    assert branding["tenant_state_id"] == "lagos"
    assert branding["custom_domain"] == "sos.lagosstate.gov.ng"
    assert branding["portal_title"] == "Lagos State One-Gov Portal"
    routing = json.loads((out / "ingress" / "host-routing.json").read_text())
    assert routing["kind"] == "TenantHostRouting"
    assert routing["spec"]["host"] == "sos.lagosstate.gov.ng"
    backends = {r["backend"]["service"] for r in routing["spec"]["routes"]}
    assert backends == {"api-gateway", "portal-frontend"}
    # manifest summary carries the domain
    assert "sos.lagosstate.gov.ng" in result.output


def test_bundle_host_routing_defaults_without_seed(tmp_path: Path) -> None:
    # taraba has no branding seed dir scenario simulated via config_root fallback
    branding = gitops.load_branding("taraba", config_root=tmp_path / "empty")
    assert branding["custom_domain"] == "sos.tarabastate.gov.ng"
    assert branding["locales"] == ["en"]
    routing = json.loads(gitops._render_host_routing("taraba", "shared", branding))
    assert routing["spec"]["host"] == "sos.tarabastate.gov.ng"
    assert routing["spec"]["routes"][1]["backend"]["namespace"] == "sos-taraba-shared"


def test_load_branding_rejects_state_mismatch(tmp_path: Path) -> None:
    bad = tmp_path / "lagos"
    bad.mkdir(parents=True)
    (bad / "branding.json").write_text(json.dumps({"tenant_state_id": "ogun"}))
    try:
        gitops.load_branding("lagos", config_root=tmp_path)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "does not match" in str(exc)


def test_tenant_branding_command(tmp_path: Path) -> None:
    result = runner.invoke(app, ["tenant", "branding", "lagos"])
    assert result.exit_code == 0, result.output
    assert "Lagos State One-Gov Portal" in result.output
    assert "sos.lagosstate.gov.ng" in result.output
    assert "yo" in result.output


def test_tenant_branding_command_all_37(tmp_path: Path) -> None:
    result = runner.invoke(app, ["tenant", "branding", "fct"])
    assert result.exit_code == 0
    assert "sos.fct.gov.ng" in result.output
    result = runner.invoke(app, ["tenant", "branding", "kano"])
    assert result.exit_code == 0
    assert "sos.kanostate.gov.ng" in result.output
    assert "ha" in result.output


def test_tenant_branding_command_rejects_unknown_state() -> None:
    assert runner.invoke(app, ["tenant", "branding", "atlantis"]).exit_code == 2
