"""Tests for the Stage 7.D backup/restore tooling.

Covers: deterministic dry-run output, manifest hashing, and fail-closed
behaviour when configuration is absent. No database, network, or boto3
is required — live paths are exercised by the DR gate on the restore host.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKUP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKUP_DIR))

import opensearch_snapshot  # noqa: E402
import postgres_backup  # noqa: E402
import restore_verify  # noqa: E402

PG = BACKUP_DIR / "postgres_backup.py"
OS = BACKUP_DIR / "opensearch_snapshot.py"
RV = BACKUP_DIR / "restore_verify.py"


def run(script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    merged = dict(os.environ)
    for var in ("SOS_BACKUP_PGDSN", "SOS_BACKUP_S3_BUCKET", "S3_KMS_KEY_ID",
                "SOS_BACKUP_VERIFY_DSN", "OPENSEARCH_URL", "OPENSEARCH_USER",
                "OPENSEARCH_PASSWORD", "OPENSEARCH_TOKEN"):
        merged.pop(var, None)
    if env:
        merged.update(env)
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, env=merged)


# ---------------------------------------------------------------------------
# postgres_backup.py
# ---------------------------------------------------------------------------

def test_postgres_dry_run_is_default_and_deterministic():
    r1 = run(PG, "--outdir", "/tmp/whatever")
    r2 = run(PG, "--outdir", "/tmp/whatever")
    assert r1.returncode == 0 and r2.returncode == 0
    assert r1.stdout == r2.stdout, "dry-run output must be byte-identical across runs"
    assert "DRY-RUN" in r1.stdout
    # All six launch tenants, sorted by schema name.
    schemas = [ln.strip().split(" ")[0] for ln in r1.stdout.splitlines()
               if ln.strip().startswith("tenant_") and "->" in ln]
    assert schemas == sorted(postgres_backup.tenant_schema(t) for t in postgres_backup.DEFAULT_TENANTS)


def test_postgres_dry_run_tenant_subset():
    r = run(PG, "--tenants", "osun,benue")
    assert r.returncode == 0
    assert "tenant_benue" in r.stdout and "tenant_osun" in r.stdout
    assert "tenant_lagos" not in r.stdout


def test_postgres_execute_fails_closed_without_dsn(tmp_path):
    r = run(PG, "--execute", "--outdir", str(tmp_path))
    assert r.returncode == 2
    assert "FAIL-CLOSED" in r.stderr
    assert "SOS_BACKUP_PGDSN" in r.stderr
    assert not (tmp_path / "manifest.json").exists()


def test_postgres_execute_fails_closed_without_pg_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(postgres_backup.shutil, "which", lambda tool: None)
    with pytest.raises(postgres_backup.BackupConfigError, match="pg_dump"):
        postgres_backup.execute(postgres_backup.build_plan(["ogun"], tmp_path),
                                tmp_path, {postgres_backup.ENV_DSN: "postgresql://x"})


def test_upload_fails_closed_without_kms_key():
    pytest.importorskip("boto3", reason="boto3 absent: import guard fires first (covered separately)")
    with pytest.raises(postgres_backup.BackupConfigError, match="S3_KMS_KEY_ID"):
        postgres_backup.upload_to_s3([], Path("."), {postgres_backup.ENV_BUCKET: "b"})


def test_upload_fails_closed_without_boto3():
    pytest.importorskip("builtins")
    import builtins
    real_import = builtins.__import__
    def no_boto3(name, *a, **k):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *a, **k)
    builtins.__import__ = no_boto3
    try:
        with pytest.raises(postgres_backup.BackupConfigError, match="boto3"):
            postgres_backup.upload_to_s3([], Path("."),
                                         {postgres_backup.ENV_BUCKET: "b",
                                          postgres_backup.ENV_KMS_KEY: "k"})
    finally:
        builtins.__import__ = real_import


def test_manifest_sha256_roundtrip(backup_dir):
    assert postgres_backup.verify_manifest(backup_dir) == []
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert [e["schema"] for e in manifest["entries"]] == ["tenant_lagos", "tenant_ogun"]
    for entry in manifest["entries"]:
        digest = postgres_backup.sha256_file(backup_dir / entry["dump_file"])
        assert entry["sha256"] == digest


def test_manifest_detects_tampered_dump(backup_dir):
    dump = backup_dir / "tenant_ogun.dump.pgc"
    dump.write_bytes(b"tampered")
    errors = postgres_backup.verify_manifest(backup_dir)
    assert any("tenant_ogun" in e for e in errors)


# ---------------------------------------------------------------------------
# restore_verify.py
# ---------------------------------------------------------------------------

def test_restore_verify_dry_run_ok_on_intact_backup(backup_dir):
    r = run(RV, "--outdir", str(backup_dir))
    assert r.returncode == 0
    assert "DRY-RUN" in r.stdout


def test_restore_verify_dry_run_deterministic(backup_dir):
    r1 = run(RV, "--outdir", str(backup_dir))
    r2 = run(RV, "--outdir", str(backup_dir))
    assert r1.stdout == r2.stdout


def test_restore_verify_nonzero_on_hash_mismatch(backup_dir):
    (backup_dir / "tenant_lagos.dump.pgc").write_bytes(b"corrupted")
    r = run(RV, "--outdir", str(backup_dir))
    assert r.returncode == 1
    assert "tenant_lagos" in r.stderr


def test_restore_verify_execute_fails_closed_without_dsn(backup_dir):
    r = run(RV, "--execute", "--outdir", str(backup_dir))
    assert r.returncode == 2
    assert "SOS_BACKUP_VERIFY_DSN" in r.stderr


def test_restore_verify_missing_manifest_fails(tmp_path):
    r = run(RV, "--outdir", str(tmp_path))
    assert r.returncode == 1
    assert "no manifest.json" in r.stderr


# ---------------------------------------------------------------------------
# opensearch_snapshot.py
# ---------------------------------------------------------------------------

def test_opensearch_dry_run_default_deterministic():
    r1 = run(OS, "--name", "drill-20990101")
    r2 = run(OS, "--name", "drill-20990101")
    assert r1.returncode == 0 and r2.returncode == 0
    assert r1.stdout == r2.stdout
    assert "DRY-RUN" in r1.stdout
    assert "_snapshot/sos-audit-worm-s3" in r1.stdout
    assert "sos-audit-*" in r1.stdout


def test_opensearch_execute_fails_closed_without_url():
    r = run(OS, "--execute")
    assert r.returncode == 2
    assert "OPENSEARCH_URL" in r.stderr


def test_opensearch_execute_fails_closed_without_credentials():
    with pytest.raises(opensearch_snapshot.SnapshotConfigError, match="credentials"):
        opensearch_snapshot._http("http://localhost:9", "GET", "_snapshot/x", None,
                                  {"OPENSEARCH_URL": "http://localhost:9"})


def test_opensearch_repo_body_matches_committed_ism_policy():
    body = opensearch_snapshot.load_repo_body()
    assert body["type"] == "s3"
    assert body["settings"]["readonly"] is True
    assert body["settings"]["object_lock"]["mode"] == "COMPLIANCE"
    assert body["settings"]["object_lock"]["retention_days"] == 2555
