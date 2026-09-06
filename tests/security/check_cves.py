#!/usr/bin/env python3
"""Stage 3 — CVE gate: zero critical/high vulnerabilities.

Reads one or more trivy JSON reports (`trivy image -f json`) and fails
(exit 1) if any CRITICAL or HIGH vulnerability is present. Supports a single
report file or a directory of `*.json` reports.

Usage:
    python3 tests/security/check_cves.py --input path/to/trivy.json
    python3 tests/security/check_cves.py --input tests/evidence/trivy/ --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

GATED_SEVERITIES = ("CRITICAL", "HIGH")


def load_reports(path: Path) -> list[tuple[str, dict]]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    reports = []
    for f in files:
        try:
            reports.append((f.name, json.loads(f.read_text())))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL: cannot parse {f}: {exc}", file=sys.stderr)
            sys.exit(2)
    if not reports:
        print(f"FAIL: no JSON reports under {path}", file=sys.stderr)
        sys.exit(2)
    return reports


def count_findings(report: dict) -> Counter:
    counts: Counter = Counter()
    for res in report.get("Results", []) or []:
        for vuln in res.get("Vulnerabilities", []) or []:
            sev = str(vuln.get("Severity", "")).upper()
            if sev in GATED_SEVERITIES:
                counts[sev] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="trivy JSON report file or directory of reports")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"FAIL: input {args.input} does not exist", file=sys.stderr)
        return 2

    total: Counter = Counter()
    per_report: dict[str, dict[str, int]] = {}
    for name, report in load_reports(args.input):
        counts = count_findings(report)
        per_report[name] = dict(counts)
        total.update(counts)

    summary = {
        "gate": "zero critical/high CVEs",
        "input": str(args.input),
        "critical": total.get("CRITICAL", 0),
        "high": total.get("HIGH", 0),
        "reports": per_report,
        "passed": not total,
    }
    print(json.dumps(summary, indent=2))
    if total:
        print(f"FAIL: {total.get('CRITICAL', 0)} CRITICAL, "
              f"{total.get('HIGH', 0)} HIGH vulnerabilities found", file=sys.stderr)
        return 1
    print("PASS: zero critical/high vulnerabilities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
