"""Tests for `sosctl ledger init-chart`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sosctl.cli import app
from sosctl.ledger import build_chart
from sosctl.states import STATE_TENANT_IDS

runner = CliRunner()


def test_init_chart_emits_bootstrap_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["ledger", "init-chart", "--state=benue",
         "--gazette-ref=BIRS-EDICT-2026-04", f"--out-dir={tmp_path}"],
    )
    assert result.exit_code == 0, result.output
    chart_path = tmp_path / "benue" / "chart-of-accounts.json"
    assert chart_path.exists()
    chart = json.loads(chart_path.read_text())
    assert chart["tenant_state_id"] == "benue"
    assert chart["gazette_reference"] == "BIRS-EDICT-2026-04"
    assert chart["ledger_id"] == 1
    assert chart["tigerbeetle_tenant_partition"] == STATE_TENANT_IDS["benue"]
    codes = {a["code"] for a in chart["accounts"]}
    assert {1001, 3001, 5001} <= codes  # clearing, CRF/TSA, federal pass-through
    transfer_codes = {t["code"] for t in chart["transfer_codes"]}
    assert 150 in transfer_codes  # hospital/education consolidated billing


def test_init_chart_account_id_seed_layout() -> None:
    chart = build_chart("lagos", "LASG-GAZ-2026-01")
    acct = next(a for a in chart["accounts"] if a["code"] == 3001)
    seed = acct["account_id_seed"]
    assert seed & 0xFFFF == STATE_TENANT_IDS["lagos"]  # tenant in bits 0..15
    assert (seed >> 32) & 0xFFFF == 3001  # account class in bits 32..47


def test_init_chart_idempotent(tmp_path: Path) -> None:
    args = ["ledger", "init-chart", "--state=osun", "--gazette-ref=OSS-2026-07", f"--out-dir={tmp_path}"]
    assert runner.invoke(app, args).exit_code == 0
    content = (tmp_path / "osun" / "chart-of-accounts.json").read_text()
    assert runner.invoke(app, args).exit_code == 0
    assert (tmp_path / "osun" / "chart-of-accounts.json").read_text() == content


def test_init_chart_rejects_bad_state_and_blank_gazette(tmp_path: Path) -> None:
    bad_state = runner.invoke(
        app, ["ledger", "init-chart", "--state=kano", "--gazette-ref=X", f"--out-dir={tmp_path}"]
    )
    assert bad_state.exit_code == 2
    blank = runner.invoke(
        app, ["ledger", "init-chart", "--state=ogun", "--gazette-ref=  ", f"--out-dir={tmp_path}"]
    )
    assert blank.exit_code == 2
