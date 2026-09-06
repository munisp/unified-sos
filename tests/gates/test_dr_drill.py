"""Tests for the DR drill gate (tests/gates/dr_drill.py).

Covers: explicit SKIP markers without a live backup environment, PASS on
fresh fabricated evidence, freshness enforcement, and fail-closed
behaviour under SOS_ENV=production.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

GATES = Path(__file__).resolve().parent
REPO_ROOT = GATES.parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools" / "backup"))

import postgres_backup  # noqa: E402

spec = importlib.util.spec_from_file_location("dr_drill", GATES / "dr_drill.py")
dr_drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr_drill)


def make_evidence(base: Path, *, age_hours: float = 1.0, report_status: str | None = "pass") -> Path:
    """Fabricate a manifest dir the way postgres_backup --execute would."""
    d = base / "run-1"
    d.mkdir(parents=True)
    entries = []
    for tenant in ("ogun",):
        schema = postgres_backup.tenant_schema(tenant)
        dump = d / f"{schema}{postgres_backup.DUMP_SUFFIX}"
        dump.write_bytes(b"drill dump payload\n")
        entries.append({"tenant": tenant, "schema": schema, "dump_file": dump.name,
                        "bytes": dump.stat().st_size,
                        "sha256": postgres_backup.sha256_file(dump), "tables": {}})
    manifest = postgres_backup.write_manifest(entries, d)
    data = json.loads(manifest.read_text())
    data["created_utc"] = (dt.datetime.now(dt.timezone.utc)
                           - dt.timedelta(hours=age_hours)).isoformat()
    manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    if report_status is not None:
        (d / "restore_verify_report.json").write_text(json.dumps({
            "status": report_status, "detail": "drill",
            "verified_utc": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
    return d


def run_gate(monkeypatch, env: dict) -> int:
    for var in ("SOS_BACKUP_MANIFEST_DIR", "SOS_BACKUP_MAX_AGE_HOURS", "SOS_ENV"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    dr_drill.results.clear()
    return dr_drill.main()


def statuses():
    return {name: status for name, status, _ in dr_drill.results}


def test_skips_without_live_env(monkeypatch, tmp_path):
    rc = run_gate(monkeypatch, {"SOS_BACKUP_MANIFEST_DIR": str(tmp_path / "empty")})
    assert rc == 0
    s = statuses()
    assert s["backup-manifests-present"] == dr_drill.SKIP_CREDS
    assert s["restore-verify-passed"] == dr_drill.SKIP_CREDS
    assert s["backup-tooling[postgres_backup.py]"] == dr_drill.PASS


def test_production_fails_closed_without_evidence(monkeypatch, tmp_path):
    rc = run_gate(monkeypatch, {"SOS_BACKUP_MANIFEST_DIR": str(tmp_path / "empty"),
                                "SOS_ENV": "production"})
    assert rc == 1
    assert statuses()["backup-manifests-present"] == dr_drill.FAIL


def test_passes_with_fresh_verified_manifest(monkeypatch, tmp_path):
    make_evidence(tmp_path, age_hours=2)
    rc = run_gate(monkeypatch, {"SOS_BACKUP_MANIFEST_DIR": str(tmp_path)})
    assert rc == 0
    s = statuses()
    assert s["backup-manifest-fresh[run-1]"] == dr_drill.PASS
    assert s["backup-manifest-integrity[run-1]"] == dr_drill.PASS
    assert s["restore-verify-passed[run-1]"] == dr_drill.PASS


def test_stale_manifest_fails(monkeypatch, tmp_path):
    make_evidence(tmp_path, age_hours=48)
    rc = run_gate(monkeypatch, {"SOS_BACKUP_MANIFEST_DIR": str(tmp_path),
                                "SOS_BACKUP_MAX_AGE_HOURS": "24"})
    assert rc == 1
    assert statuses()["backup-manifest-fresh[run-1]"] == dr_drill.FAIL


def test_failed_restore_report_fails(monkeypatch, tmp_path):
    make_evidence(tmp_path, report_status="fail")
    rc = run_gate(monkeypatch, {"SOS_BACKUP_MANIFEST_DIR": str(tmp_path)})
    assert rc == 1
    assert statuses()["restore-verify-passed[run-1]"] == dr_drill.FAIL


def test_tampered_dump_fails_integrity(monkeypatch, tmp_path):
    d = make_evidence(tmp_path)
    (d / "tenant_ogun.dump.pgc").write_bytes(b"tampered")
    rc = run_gate(monkeypatch, {"SOS_BACKUP_MANIFEST_DIR": str(tmp_path)})
    assert rc == 1
    assert statuses()["backup-manifest-integrity[run-1]"] == dr_drill.FAIL


def test_wired_into_run_gates():
    import run_gates  # noqa: E402  (sys.path: this dir under pytest rootdir)
    assert "dr" in run_gates.GATES
    # --gate dr executes without live env and exits 0 (skips, not failures).
    rc = subprocess_run_dr()
    assert rc == 0


def subprocess_run_dr() -> int:
    import subprocess
    proc = subprocess.run([sys.executable, str(GATES / "run_gates.py"), "--gate", "dr"],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    assert "VERDICT: PASS" in proc.stdout, proc.stdout + proc.stderr
    return proc.returncode
