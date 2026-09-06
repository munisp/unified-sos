#!/usr/bin/env python3
"""DR drill gate (Stage 7.D) — standalone, invoked by run_gates.py --gate dr.

Checks:
  1. Backup tooling exists and compiles (tools/backup/*.py).
  2. Backup manifests exist and are fresh (default max age 24h,
     SOS_BACKUP_MAX_AGE_HOURS to override). Manifest directory:
     SOS_BACKUP_MANIFEST_DIR, else tests/evidence/backups/postgres.
  3. Each manifest entry hashes correctly against its dump file
     (manifest integrity, no database needed).
  4. Restore-verify evidence exists and passed: a restore-verify report
     (restore_verify_report.json with "status": "pass") in the manifest
     directory, produced by an --execute run of restore_verify.py on the
     restore host.

Without a live backup environment, checks 2–4 are explicit
SKIPPED_NO_CREDENTIALS markers — never a silent pass. With
SOS_ENV=production any missing dependency FAILS the gate (fail-closed).

Exit 0 = pass/skip, 1 = at least one FAIL.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import py_compile
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_BACKUP = REPO_ROOT / "tools" / "backup"
DEFAULT_MANIFEST_DIR = REPO_ROOT / "tests" / "evidence" / "backups" / "postgres"

ENV_MANIFEST_DIR = "SOS_BACKUP_MANIFEST_DIR"
ENV_MAX_AGE_HOURS = "SOS_BACKUP_MAX_AGE_HOURS"

PASS, FAIL = "PASS", "FAIL"
SKIP_CREDS = "SKIPPED_NO_CREDENTIALS"

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    line = f"[{status}] {name}" + (f" — {detail}" if detail else "")
    print(line, flush=True)


def production_mode() -> bool:
    return os.environ.get("SOS_ENV", "").lower() == "production"


def skip_or_fail(name: str, reason: str) -> None:
    if production_mode():
        record(name, FAIL, f"{reason} (SOS_ENV=production: missing dependencies FAIL the gate)")
    else:
        record(name, SKIP_CREDS, reason)


def check_tooling() -> None:
    scripts = sorted(TOOLS_BACKUP.glob("*.py"))
    if not scripts:
        record("backup-tooling-present", FAIL, f"no scripts in {TOOLS_BACKUP}")
        return
    for script in scripts:
        try:
            py_compile.compile(str(script), doraise=True)
            record(f"backup-tooling[{script.name}]", PASS, "compiles")
        except py_compile.PyCompileError as exc:
            record(f"backup-tooling[{script.name}]", FAIL, str(exc))


def manifest_dirs() -> list[Path]:
    base = Path(os.environ.get(ENV_MANIFEST_DIR, str(DEFAULT_MANIFEST_DIR)))
    if (base / "manifest.json").is_file():
        return [base]
    return sorted(p for p in base.glob("*") if (p / "manifest.json").is_file()) if base.is_dir() else []


def check_manifests() -> None:
    sys.path.insert(0, str(TOOLS_BACKUP))
    from postgres_backup import verify_manifest  # local import after sys.path fix

    dirs = manifest_dirs()
    if not dirs:
        skip_or_fail("backup-manifests-present",
                     f"no manifest.json under {os.environ.get(ENV_MANIFEST_DIR, DEFAULT_MANIFEST_DIR)} "
                     f"(set {ENV_MANIFEST_DIR}); backups not run in this environment")
        return
    max_age = float(os.environ.get(ENV_MAX_AGE_HOURS, "24"))
    now = dt.datetime.now(dt.timezone.utc)
    for d in dirs:
        manifest = json.loads((d / "manifest.json").read_text())
        created = dt.datetime.fromisoformat(manifest["created_utc"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        age_h = (now - created).total_seconds() / 3600
        status = PASS if age_h <= max_age else FAIL
        record(f"backup-manifest-fresh[{d.name}]", status,
               f"age {age_h:.1f}h (max {max_age}h), {len(manifest.get('entries', []))} tenant(s)")
        errors = verify_manifest(d)
        record(f"backup-manifest-integrity[{d.name}]", FAIL if errors else PASS,
               "; ".join(errors) if errors else "all dump hashes match manifest")


def check_restore_verify() -> None:
    dirs = manifest_dirs()
    if not dirs:
        skip_or_fail("restore-verify-passed",
                     "no backup manifests to verify restore evidence against")
        return
    for d in dirs:
        report = d / "restore_verify_report.json"
        if not report.is_file():
            skip_or_fail(f"restore-verify-passed[{d.name}]",
                         f"no restore_verify_report.json in {d} (drill not run here)")
            continue
        data = json.loads(report.read_text())
        status = PASS if data.get("status") == "pass" else FAIL
        record(f"restore-verify-passed[{d.name}]", status, data.get("detail", ""))


def main() -> int:
    check_tooling()
    check_manifests()
    check_restore_verify()
    failed = [r for r in results if r[1] == FAIL]
    print(f"VERDICT: {'FAIL' if failed else 'PASS'} "
          f"({sum(1 for r in results if r[1] == PASS)} passed, {len(failed)} failed, "
          f"{sum(1 for r in results if r[1].startswith('SKIPPED'))} skipped)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
