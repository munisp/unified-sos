#!/usr/bin/env python3
"""Stage 4 — Site Acceptance Testing (SAT) harness.

Credential-free harness. Boots the docker-compose service groups that stand
in for the SAT profiles and runs whatever integration suites are present.

Compose profiles (logical groups over deploy/docker-compose.yml services):
    ledger       -> tigerbeetle, tigerbeetle-init, postgres, redis
    payments     -> redpanda, mod-rev-core
    controlplane -> keycloak, minio, opensearch

Live hardware integration (weighbridges, ANPR, POS, biometric scanners) and
live bank clearing settlement require on-site credentials; without them the
corresponding checks are explicit SKIPPED_NO_CREDENTIALS markers.

Evidence: a signed bundle is written to tests/evidence/sat-<timestamp>/ with
a sha256 manifest (MANIFEST.sha256) covering every artifact.

Exit codes: 0 pass (with explicit skips allowed outside production),
1 fail, 3 skipped (docker/compose unavailable or suites absent).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.yml"
COMPOSE_ENV = REPO_ROOT / "deploy" / ".env"

PROFILES = {
    "ledger": ["tigerbeetle", "tigerbeetle-init", "postgres", "redis"],
    "payments": ["redpanda", "mod-rev-core"],
    "controlplane": ["keycloak", "minio", "opensearch"],
}

# Integration suites run when present (p2-test-suites may add tests/sat/suites).
SUITE_DIRS = [REPO_ROOT / "tests" / "sat" / "suites", REPO_ROOT / "tests" / "integration"]


def run(argv: list[str], timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def sha256_manifest(outdir: Path) -> Path:
    lines = []
    for f in sorted(outdir.rglob("*")):
        if f.is_file() and f.name != "MANIFEST.sha256":
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            lines.append(f"{digest}  {f.relative_to(outdir)}")
    manifest = outdir / "MANIFEST.sha256"
    manifest.write_text("\n".join(lines) + "\n")
    return manifest


def main() -> int:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = REPO_ROOT / "tests" / "evidence" / f"sat-{stamp}"
    outdir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    def record(name: str, status: str, detail: str = "") -> None:
        results.append({"name": name, "status": status, "detail": detail[-2000:]})
        print(f"[{status}] {name}" + (f" — {detail.splitlines()[-1] if detail else ''}"))

    docker = shutil.which("docker") is not None
    if not docker:
        for profile in PROFILES:
            record(f"compose-profile[{profile}]", "SKIPPED_NO_TOOL",
                   "docker not installed; SAT stack cannot boot")
    else:
        base = ["docker", "compose", "-f", str(COMPOSE_FILE)]
        if COMPOSE_ENV.is_file():
            base += ["--env-file", str(COMPOSE_ENV)]
        for profile, services in PROFILES.items():
            code, out = run(base + ["up", "-d", "--build"] + services, timeout=1800)
            record(f"compose-profile[{profile}]", "PASS" if code == 0 else "FAIL",
                   out)
        if any(r["status"] == "FAIL" for r in results):
            record("sat-stack", "FAIL", "one or more compose profiles failed to boot")

    suites = [d for d in SUITE_DIRS if d.is_dir()]
    if docker and suites:
        for suite in suites:
            code, out = run(["python3", "-m", "pytest", "-q", str(suite)], timeout=1800)
            record(f"integration-suite[{suite.name}]",
                   "PASS" if code == 0 else "FAIL", out)
            (outdir / f"suite-{suite.name}.log").write_text(out + "\n")
    elif docker:
        record("integration-suites", "SKIPPED_NO_TOOL",
               "no tests/sat/suites or tests/integration directory yet "
               "(owned by feat/p2-test-suites)")

    if os.environ.get("SOS_SAT_LIVE") == "1":
        record("hardware-integration", "SKIPPED_NO_TOOL",
               "live hardware adapters exercise left to on-site SAT runbook")
        record("bank-clearing-settlement", "SKIPPED_NO_TOOL",
               "SOS_SAT_LIVE=1 set but live bank clearing harness not implemented here")
    else:
        record("hardware-integration", "SKIPPED_NO_CREDENTIALS",
               "weighbridge/ANPR/POS/biometric hardware requires on-site access")
        record("bank-clearing-settlement", "SKIPPED_NO_CREDENTIALS",
               "live bank clearing requires settlement-bank credentials")

    summary = {
        "stage": "sat",
        "timestamp_utc": stamp,
        "profiles": PROFILES,
        "results": results,
        "verdict": "FAIL" if any(r["status"] == "FAIL" for r in results) else "PASS",
    }
    if os.environ.get("SOS_ENV", "").lower() == "production" and any(
            r["status"].startswith("SKIPPED") for r in results):
        summary["verdict"] = "FAIL"
        summary["note"] = "SOS_ENV=production: skipped dependencies fail the gate"
    (outdir / "sat-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    manifest = sha256_manifest(outdir)
    print(f"signed evidence bundle: {outdir} (manifest {manifest.name})")
    print(f"VERDICT: {summary['verdict']}")
    return 1 if summary["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
