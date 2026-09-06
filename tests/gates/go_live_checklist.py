#!/usr/bin/env python3
"""Stage 5 — Go-live gate checklist.

Encodes the four go-live gates from tests/README.md as artifact checks.
Exits non-zero unless ALL artifacts are present and valid; sign-off metadata
is recorded into a signed JSON attestation with a sha256 manifest.

Usage:
    python3 tests/gates/go_live_checklist.py [--artifacts-dir DIR]
        [--signoff "Name=Role"]...   (informational; recorded in attestation)

Expected artifacts in --artifacts-dir (default tests/evidence/go-live/):
    1. reconciliation-report.{json,md,csv} + reconciliation-report.sha256
       100% legacy <-> TigerBeetle reconciliation; .sha256 pins the report hash.
    2. cve-report.json — trivy JSON; zero CRITICAL/HIGH (verified inline).
    3. certification-roster.csv — >= 200 certified officers (header + rows;
       a `certified` column, if present, must be truthy for counted rows).
    4. escrow-attestation.json — {"performance_bond_lodged": true,
       "escrow_configured": true, "concession_gazetted": true}
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "tests" / "evidence" / "go-live"
MIN_CERTIFIED_OFFICERS = 200


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_reconciliation(adir: Path, failures: list[str]) -> dict:
    for ext in ("json", "md", "csv"):
        report = adir / f"reconciliation-report.{ext}"
        if report.is_file():
            break
    else:
        failures.append("gate1: reconciliation-report.{json,md,csv} missing "
                        "(100% legacy<->TigerBeetle reconciliation evidence)")
        return {}
    pin = adir / "reconciliation-report.sha256"
    if not pin.is_file():
        failures.append("gate1: reconciliation-report.sha256 missing (report hash pin)")
        return {"report": report.name}
    recorded = pin.read_text().split()[0].strip()
    actual = sha256_of(report)
    if recorded.lower() != actual:
        failures.append(f"gate1: reconciliation report hash mismatch "
                        f"(recorded {recorded[:12]}… != actual {actual[:12]}…)")
    return {"report": report.name, "sha256": actual}


def check_cves(adir: Path, failures: list[str]) -> dict:
    report = adir / "cve-report.json"
    if not report.is_file():
        failures.append("gate2: cve-report.json missing (trivy JSON; zero critical/high required)")
        return {}
    try:
        doc = json.loads(report.read_text())
    except json.JSONDecodeError as exc:
        failures.append(f"gate2: cve-report.json is not valid JSON: {exc}")
        return {"report": report.name}
    counts = {"CRITICAL": 0, "HIGH": 0}
    for res in doc.get("Results", []) or []:
        for vuln in res.get("Vulnerabilities", []) or []:
            sev = str(vuln.get("Severity", "")).upper()
            if sev in counts:
                counts[sev] += 1
    if counts["CRITICAL"] or counts["HIGH"]:
        failures.append(f"gate2: {counts['CRITICAL']} CRITICAL / {counts['HIGH']} HIGH "
                        f"CVEs unresolved (must be zero)")
    return {"report": report.name, **counts}


def check_roster(adir: Path, failures: list[str]) -> dict:
    roster = adir / "certification-roster.csv"
    if not roster.is_file():
        failures.append("gate3: certification-roster.csv missing "
                        f"(>= {MIN_CERTIFIED_OFFICERS} certified officers required)")
        return {}
    rows = list(csv.DictReader(roster.read_text().splitlines()))
    if not rows:
        failures.append("gate3: certification-roster.csv has no data rows")
        return {"report": roster.name, "certified": 0}
    if "certified" in rows[0]:
        certified = sum(1 for r in rows
                        if (r.get("certified") or "").strip().lower()
                        in ("1", "true", "yes", "y"))
    else:
        certified = len(rows)
    if certified < MIN_CERTIFIED_OFFICERS:
        failures.append(f"gate3: only {certified} certified officers "
                        f"(< {MIN_CERTIFIED_OFFICERS} required)")
    return {"report": roster.name, "certified": certified}


def check_escrow(adir: Path, failures: list[str]) -> dict:
    attest = adir / "escrow-attestation.json"
    if not attest.is_file():
        failures.append("gate4: escrow-attestation.json missing (performance bond / "
                        "escrow config / concession gazette)")
        return {}
    try:
        doc = json.loads(attest.read_text())
    except json.JSONDecodeError as exc:
        failures.append(f"gate4: escrow-attestation.json is not valid JSON: {exc}")
        return {"report": attest.name}
    for key in ("performance_bond_lodged", "escrow_configured", "concession_gazetted"):
        if doc.get(key) is not True:
            failures.append(f"gate4: escrow attestation '{key}' is not true")
    return {"report": attest.name, **{k: doc.get(k) for k in
            ("performance_bond_lodged", "escrow_configured", "concession_gazetted")}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--signoff", action="append", default=[],
                        help='"Name=Role" sign-off metadata (recorded, not verified)')
    args = parser.parse_args()

    adir = args.artifacts_dir
    failures: list[str] = []
    evidence: dict = {}
    if not adir.is_dir():
        failures.append(f"artifacts directory {adir} does not exist")
    else:
        evidence["gate1_reconciliation"] = check_reconciliation(adir, failures)
        evidence["gate2_cves"] = check_cves(adir, failures)
        evidence["gate3_roster"] = check_roster(adir, failures)
        evidence["gate4_escrow"] = check_escrow(adir, failures)

    signoffs = []
    for s in args.signoff:
        if "=" in s:
            name, role = s.split("=", 1)
            signoffs.append({"name": name.strip(), "role": role.strip()})

    verdict = "FAIL" if failures else "PASS"
    attestation = {
        "stage": "golive",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "artifacts_dir": str(adir),
        "evidence": evidence,
        "signoffs": signoffs,
        "failures": failures,
        "verdict": verdict,
    }
    blob = json.dumps(attestation, indent=2) + "\n"
    attestation["attestation_sha256"] = hashlib.sha256(blob.encode()).hexdigest()
    out = json.dumps(attestation, indent=2) + "\n"
    print(out)
    if adir.is_dir():
        (adir / "go-live-attestation.json").write_text(out)
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    print(f"VERDICT: {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
