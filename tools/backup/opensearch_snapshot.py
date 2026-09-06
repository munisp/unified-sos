#!/usr/bin/env python3
"""OpenSearch audit-archive snapshot orchestration (Stage 7.D).

The control-plane audit archive (services/control-plane audit_archive.py)
is a WORM sink: ``sos-audit-*`` indices are snapshotted daily to an S3
repository with Object Lock (COMPLIANCE mode, 7-year retention) per the
ISM policy in ``infra/helm/opensearch/ism-policy.yaml``.

This script:
  1. Registers/updates the snapshot repository (PUT _snapshot/<repo>)
     from the committed ``snapshot-repository.json`` in that ConfigMap.
  2. Optionally triggers an ad-hoc snapshot (PUT
     _snapshot/<repo>/<name>?wait_for_completion=true) — the ISM policy
     hook already performs the scheduled daily snapshot; this is the
     manual/drill path.
  3. Verifies the last snapshot exists and is ``SUCCESS``.

Modes
-----
* ``--dry-run`` (**default**): print the exact HTTP requests (method,
  path, body) that would be issued. Deterministic; no network.
* ``--execute``: issue the requests over ``urllib`` (no extra deps).
  Requires ``OPENSEARCH_URL`` (basic auth via ``OPENSEARCH_USER`` /
  ``OPENSEARCH_PASSWORD`` or a bearer ``OPENSEARCH_TOKEN``) — fail-closed
  when credentials are absent.

Exit codes: 0 = ok, 1 = snapshot verification failed, 2 = fail-closed
configuration error.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ISM_CONFIGMAP = REPO_ROOT / "infra" / "helm" / "opensearch" / "ism-policy.yaml"

REPO_NAME = "sos-audit-worm-s3"
INDEX_PATTERN = "sos-audit-*"


class SnapshotConfigError(RuntimeError):
    """Fail-closed configuration error."""


def load_repo_body(configmap: Path = ISM_CONFIGMAP) -> dict:
    """Extract the snapshot-repository.json payload from the ConfigMap."""
    import yaml  # repo-wide dependency (used by infra validators)

    docs = [d for d in yaml.safe_load_all(configmap.read_text()) if d]
    for doc in docs:
        data = doc.get("data", {}) if isinstance(doc, dict) else {}
        if "snapshot-repository.json" in data:
            return json.loads(data["snapshot-repository.json"])
    raise SnapshotConfigError(f"no snapshot-repository.json found in {configmap}")


def planned_requests(repo_body: dict, snapshot_name: str) -> list[dict]:
    """The exact HTTP plan, in order. Deterministic for a fixed name."""
    return [
        {"method": "PUT", "path": f"_snapshot/{REPO_NAME}", "body": repo_body,
         "purpose": "register/refresh WORM snapshot repository"},
        {"method": "PUT",
         "path": f"_snapshot/{REPO_NAME}/{snapshot_name}?wait_for_completion=true",
         "body": {"indices": INDEX_PATTERN, "ignore_unavailable": False,
                  "include_global_state": False},
         "purpose": "trigger ad-hoc snapshot of audit indices"},
        {"method": "GET", "path": f"_snapshot/{REPO_NAME}/{snapshot_name}", "body": None,
         "purpose": "verify snapshot state == SUCCESS"},
    ]


def _http(url: str, method: str, path: str, body: dict | None, env: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    token = env.get("OPENSEARCH_TOKEN", "").strip()
    user = env.get("OPENSEARCH_USER", "").strip()
    password = env.get("OPENSEARCH_PASSWORD", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif user and password:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    else:
        raise SnapshotConfigError(
            "no OpenSearch credentials: set OPENSEARCH_TOKEN or "
            "OPENSEARCH_USER + OPENSEARCH_PASSWORD (fail-closed)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url.rstrip("/") + "/" + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise SnapshotConfigError(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()[:300]}")


def execute(plan: list[dict], env: dict) -> None:
    url = env.get("OPENSEARCH_URL", "").strip()
    if not url:
        raise SnapshotConfigError("OPENSEARCH_URL is required for --execute (fail-closed)")
    for step in plan:
        resp = _http(url, step["method"], step["path"], step["body"], env)
        print(f"ok: {step['purpose']}")
        if step["method"] == "GET":
            snaps = resp.get("snapshots", [])
            states = {s.get("snapshot"): s.get("state") for s in snaps}
            if not snaps or any(s.get("state") != "SUCCESS" for s in snaps):
                raise SnapshotConfigError(f"snapshot verification failed: states={states}")
            print(f"snapshot states: {states}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="issue the HTTP requests (default is dry-run)")
    parser.add_argument("--verify-only", action="store_true",
                        help="only verify the latest snapshot (skip register/trigger)")
    parser.add_argument("--name", default=None,
                        help="snapshot name (default: drill-<UTC date>)")
    args = parser.parse_args(argv)

    try:
        repo_body = load_repo_body()
        name = args.name or "drill-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
        plan = planned_requests(repo_body, name)
        if args.verify_only:
            plan = [p for p in plan if p["method"] == "GET"]
        if not args.execute:
            print(f"DRY-RUN OpenSearch snapshot plan (repo={REPO_NAME}, indices={INDEX_PATTERN}):")
            for step in plan:
                print(f"  {step['method']} {step['path']}  # {step['purpose']}")
                if step["body"] is not None:
                    print("    body: " + json.dumps(step["body"], sort_keys=True))
            print("re-run with --execute (requires OPENSEARCH_URL + credentials)")
            return 0
        execute(plan, dict(os.environ))
        return 0
    except SnapshotConfigError as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
