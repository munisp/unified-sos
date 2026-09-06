"""Tests for `sosctl tenant create|list|status|suspend`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

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


def test_tenant_create_generates_gitops_bundle(tmp_path: Path) -> None:
    result = _create("nasarawa", "shared", tmp_path)
    assert result.exit_code == 0, result.output
    out = tmp_path / "gitops"
    assert (out / "k8s" / "namespace.yaml").exists()
    assert (out / "postgres" / "schema-rls.sql").exists()
    assert (out / "keycloak" / "realm.json").exists()
    assert (out / "s3" if False else out / "storage" / "s3-kms.json").exists()
    ns = (out / "k8s" / "namespace.yaml").read_text()
    assert "sos-nasarawa-shared" in ns
    sql = (out / "postgres" / "schema-rls.sql").read_text()
    assert "ROW LEVEL SECURITY" in sql and "tenant_nasarawa" in sql
    realm = json.loads((out / "keycloak" / "realm.json").read_text())
    assert realm["realm"] == "sos-nasarawa"
    # Manifest summary printed.
    assert "sos-nasarawa-keyring" in result.output


def test_tenant_create_idempotent(tmp_path: Path) -> None:
    assert _create("ogun", "hybrid", tmp_path).exit_code == 0
    out = tmp_path / "gitops"
    snapshot = {p: p.read_text() for p in sorted(out.rglob("*")) if p.is_file()}
    assert _create("ogun", "hybrid", tmp_path).exit_code == 0
    snapshot2 = {p: p.read_text() for p in sorted(out.rglob("*")) if p.is_file()}
    assert snapshot == snapshot2


def test_tenant_create_rejects_bad_state_and_tier(tmp_path: Path) -> None:
    assert _create("kano", "shared", tmp_path).exit_code == 2
    assert _create("ogun", "gold", tmp_path).exit_code == 2


def test_tenant_list_status_suspend(tmp_path: Path) -> None:
    reg = tmp_path / "reg" / "tenants.json"
    assert _create("lagos", "dedicated", tmp_path).exit_code == 0
    assert _create("taraba", "shared", tmp_path).exit_code == 0

    listed = runner.invoke(app, ["tenant", "list", f"--registry={reg}"])
    assert listed.exit_code == 0
    assert "lagos" in listed.output and "taraba" in listed.output

    status = runner.invoke(app, ["tenant", "status", "--state=lagos", f"--registry={reg}"])
    assert status.exit_code == 0 and "dedicated" in status.output

    missing = runner.invoke(app, ["tenant", "status", "--state=osun", f"--registry={reg}"])
    assert missing.exit_code == 1

    suspended = runner.invoke(
        app,
        ["tenant", "suspend", "--state=taraba", "--reason=concession breach", f"--registry={reg}"],
    )
    assert suspended.exit_code == 0
    record = json.loads(reg.read_text())["tenants"]["taraba"]
    assert record["status"] == "suspended"
    assert record["suspend_reason"] == "concession breach"

    again = runner.invoke(
        app, ["tenant", "suspend", "--state=taraba", "--reason=still breached", f"--registry={reg}"]
    )
    assert again.exit_code == 0  # idempotent
    missing_suspend = runner.invoke(
        app, ["tenant", "suspend", "--state=benue", "--reason=x", f"--registry={reg}"]
    )
    assert missing_suspend.exit_code == 1


def test_tenant_list_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["tenant", "list", f"--registry={tmp_path / 'none.json'}"])
    assert result.exit_code == 0 and "No tenants" in result.output
