#!/usr/bin/env python3
"""Restore verification for per-tenant Postgres backups (Stage 7.D).

Verifies that a backup directory produced by ``postgres_backup.py`` can
actually be restored. Dumps are restored into a **scratch database**
(``SOS_BACKUP_VERIFY_DSN`` — never the source cluster; the runbook
requires an isolated restore host) where the tenant schema is recreated
by the dump itself. Per-table row counts and content SHA-256s are then
compared against the fingerprints recorded in ``manifest.json`` at backup
time. Any mismatch exits non-zero.

Modes
-----
* ``--dry-run`` (**default**): filesystem-only verification. Re-hashes
  every dump against ``manifest.json`` and re-checks sizes. Deterministic;
  no database required.
* ``--execute``: restore each dump into the scratch database and diff
  fingerprints. Requires ``SOS_BACKUP_VERIFY_DSN`` plus the ``psql`` and
  ``pg_restore`` binaries. Restored schemas are dropped after comparison
  unless ``--keep`` is given.

Exit codes: 0 = verified, 1 = mismatch, 2 = fail-closed configuration
error (missing DSN / tools / manifest).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Reuse manifest + hashing helpers from the sibling script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from postgres_backup import (  # noqa: E402
    BackupConfigError,
    table_fingerprints,
    verify_manifest,
)

ENV_VERIFY_DSN = "SOS_BACKUP_VERIFY_DSN"


class RestoreMismatch(RuntimeError):
    pass


def execute_verify(outdir: Path, env: dict, *, keep: bool = False) -> None:
    dsn = env.get(ENV_VERIFY_DSN, "").strip()
    if not dsn:
        raise BackupConfigError(f"{ENV_VERIFY_DSN} is required for --execute (fail-closed)")
    for tool in ("psql", "pg_restore"):
        if shutil.which(tool) is None:
            raise BackupConfigError(f"{tool} binary not found on PATH (fail-closed)")

    manifest = json.loads((outdir / "manifest.json").read_text())
    mismatches: list[str] = []
    for entry in manifest.get("entries", []):
        schema = entry["schema"]
        dump = outdir / entry["dump_file"]
        subprocess.run(
            ["psql", "-X", dsn, "-c", f"DROP SCHEMA IF EXISTS \"{schema}\" CASCADE"],
            capture_output=True)
        proc = subprocess.run(
            ["pg_restore", "--no-owner", "--no-privileges", "-d", dsn, str(dump)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            mismatches.append(f"{schema}: pg_restore failed: {proc.stderr.strip()}")
            continue
        restored = table_fingerprints(dsn, schema)
        expected = entry.get("tables", {})
        if restored != expected:
            for table in sorted(set(restored) | set(expected)):
                if restored.get(table) != expected.get(table):
                    mismatches.append(
                        f"{schema}.{table}: manifest {expected.get(table)} != restored {restored.get(table)}")
        if not keep:
            subprocess.run(
                ["psql", "-X", dsn, "-c", f"DROP SCHEMA IF EXISTS \"{schema}\" CASCADE"],
                capture_output=True)
    if mismatches:
        _write_report(outdir, "fail", "\n".join(mismatches))
        raise RestoreMismatch("restore verification FAILED:\n" + "\n".join(mismatches))
    _write_report(outdir, "pass", "row counts + content hashes match manifest")


def _write_report(outdir: Path, status: str, detail: str) -> None:
    """Evidence consumed by the DR gate (tests/gates/dr_drill.py)."""
    report = {
        "status": status,
        "detail": detail,
        "verified_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (outdir / "restore_verify_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="restore into the scratch database and diff (default is dry-run)")
    parser.add_argument("--keep", action="store_true",
                        help="keep restored schemas in the scratch database after verification")
    parser.add_argument("--outdir", type=Path, default=Path("backups/postgres"),
                        help="backup directory containing manifest.json + dumps")
    args = parser.parse_args(argv)

    try:
        errors = verify_manifest(args.outdir)
        if errors:
            raise RestoreMismatch("manifest integrity FAILED:\n" + "\n".join(errors))
        if not args.execute:
            print(f"DRY-RUN restore verification: manifest + dump hashes OK in {args.outdir}")
            print("re-run with --execute to restore into the scratch database and diff row counts/hashes")
            return 0
        execute_verify(args.outdir, dict(os.environ), keep=args.keep)
        print("restore verification PASSED (row counts + content hashes match manifest)")
        return 0
    except RestoreMismatch as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except BackupConfigError as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
