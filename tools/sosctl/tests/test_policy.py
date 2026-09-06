"""Tests for `sosctl policy validate|apply` and guardrail checks."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sosctl.cli import app
from sosctl.policy import guardrail_errors, validate_policy_file

from conftest import valid_pack

runner = CliRunner()


def test_validate_valid_pack(pack_file: Path, schema_path: Path) -> None:
    result = runner.invoke(
        app, ["policy", "validate", f"--file={pack_file}", f"--schema={schema_path}"]
    )
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_default_schema_resolution(pack_file: Path, monkeypatch) -> None:
    # Default schema path is repo-relative; run from repo root.
    from conftest import REPO_ROOT

    monkeypatch.chdir(REPO_ROOT)
    result = runner.invoke(app, ["policy", "validate", f"--file={pack_file.resolve()}"])
    assert result.exit_code == 0, result.output


def test_validate_instant_sum_over_100(write_pack, schema_path: Path) -> None:
    doc = valid_pack()
    doc["statutory_split_rules"][1]["split_percentage"] = 40.0  # 80 + 40 = 120 INSTANT
    f = write_pack(doc)
    result = runner.invoke(app, ["policy", "validate", f"--file={f}", f"--schema={schema_path}"])
    assert result.exit_code == 1
    assert "INSTANT" in result.output


def test_validate_ceiling_lagos_8(write_pack, schema_path: Path) -> None:
    doc = valid_pack(state="lagos")
    doc["concession_guardrails"]["revenue_share_ceiling_pct"] = 10.0
    f = write_pack(doc)
    result = runner.invoke(app, ["policy", "validate", f"--file={f}", f"--schema={schema_path}"])
    assert result.exit_code == 1
    assert "8%" in result.output


def test_validate_ceiling_agrarian_15(write_pack, schema_path: Path) -> None:
    doc = valid_pack(state="benue")
    doc["concession_guardrails"]["revenue_share_ceiling_pct"] = 15.0
    f = write_pack(doc)
    ok = runner.invoke(app, ["policy", "validate", f"--file={f}", f"--schema={schema_path}"])
    assert ok.exit_code == 0, ok.output
    doc["concession_guardrails"]["revenue_share_ceiling_pct"] = 15.5
    f2 = write_pack(doc, "bad.json")
    bad = runner.invoke(app, ["policy", "validate", f"--file={f2}", f"--schema={schema_path}"])
    assert bad.exit_code == 1 and "15%" in bad.output


def test_validate_account_code_band(write_pack, schema_path: Path) -> None:
    doc = valid_pack()
    doc["statutory_split_rules"][0]["tigerbeetle_account_code"] = 999  # below band -> schema fails
    f = write_pack(doc)
    result = runner.invoke(app, ["policy", "validate", f"--file={f}", f"--schema={schema_path}"])
    assert result.exit_code == 1


def test_guardrail_account_code_direct() -> None:
    doc = valid_pack()
    doc["statutory_split_rules"][0]["tigerbeetle_account_code"] = 10000  # guardrail layer
    errors = guardrail_errors(doc)
    assert any("1000-9999" in e for e in errors)


def test_validate_schema_failure(write_pack, schema_path: Path) -> None:
    doc = valid_pack()
    doc["policy_id"] = "lowercase-not-allowed"
    f = write_pack(doc)
    result = runner.invoke(app, ["policy", "validate", f"--file={f}", f"--schema={schema_path}"])
    assert result.exit_code == 1 and "schema:" in result.output


def test_validate_missing_file(schema_path: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["policy", "validate", f"--file={tmp_path / 'nope.json'}", f"--schema={schema_path}"]
    )
    assert result.exit_code == 1


def test_apply_stages_valid_pack(pack_file: Path, schema_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "config" / "states"
    result = runner.invoke(
        app,
        ["policy", "apply", "--state=ogun", f"--file={pack_file}",
         f"--schema={schema_path}", f"--out-dir={out}"],
    )
    assert result.exit_code == 0, result.output
    staged = out / "ogun" / "policy-pack.json"
    assert staged.exists()
    assert json.loads(staged.read_text())["tenant_state_id"] == "ogun"
    # Idempotent re-apply
    again = runner.invoke(
        app,
        ["policy", "apply", "--state=ogun", f"--file={pack_file}",
         f"--schema={schema_path}", f"--out-dir={out}"],
    )
    assert again.exit_code == 0


def test_apply_rejects_invalid_and_state_mismatch(write_pack, schema_path: Path, tmp_path: Path) -> None:
    doc = valid_pack()
    doc["statutory_split_rules"][0]["split_percentage"] = 100.0  # 100 + 12 INSTANT = 112
    f = write_pack(doc)
    bad = runner.invoke(
        app,
        ["policy", "apply", "--state=ogun", f"--file={f}",
         f"--schema={schema_path}", f"--out-dir={tmp_path / 'o'}"],
    )
    assert bad.exit_code == 1
    assert not (tmp_path / "o" / "ogun" / "policy-pack.json").exists()

    mismatch = runner.invoke(
        app,
        ["policy", "apply", "--state=osun", f"--file={write_pack(valid_pack())}",
         f"--schema={schema_path}", f"--out-dir={tmp_path / 'o2'}"],
    )
    assert mismatch.exit_code == 1 and "state mismatch" in mismatch.output


def test_validate_policy_file_invalid_json(tmp_path: Path, schema_path: Path) -> None:
    f = tmp_path / "broken.json"
    f.write_text("{not json")
    result = validate_policy_file(f, schema_path)
    assert not result.ok and "invalid JSON" in result.errors[0]
