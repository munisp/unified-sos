"""Retention manifest validation test (P1 WS4): the OpenSearch ISM policy
must enforce 7-year retention with WORM S3 snapshots before deletion."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "infra" / "helm" / "opensearch" / "ism-policy.yaml"


def _load():
    docs = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]
    cm = next(d for d in docs if d.get("kind") == "ConfigMap")
    policy = json.loads(cm["data"]["ism-policy.json"])["policy"]
    repo = json.loads(cm["data"]["snapshot-repository.json"])
    return policy, repo


def test_manifest_exists_and_parses() -> None:
    assert MANIFEST.exists(), "infra/helm/opensearch/ism-policy.yaml missing"
    policy, _ = _load()
    assert "sos-audit-*" in policy["ism_template"]["index_patterns"]


def test_seven_year_retention_before_delete() -> None:
    policy, _ = _load()
    states = {s["name"]: s for s in policy["states"]}
    assert "delete" in states
    ages = [
        t["conditions"]["min_index_age"]
        for s in states.values()
        for t in s.get("transitions", [])
        if t.get("state_name") == "delete"
    ]
    assert any(int(a.rstrip("d")) >= 2555 for a in ages), f"retention < 7y: {ages}"


def test_worm_snapshot_to_s3() -> None:
    policy, repo = _load()
    assert repo["type"] == "s3"
    lock = repo["settings"]["object_lock"]
    assert lock["mode"] == "COMPLIANCE"
    assert int(lock["retention_days"]) >= 2555
    states = {s["name"]: s for s in policy["states"]}
    delete_actions = states["delete"]["actions"]
    # Final WORM snapshot precedes deletion; indices are read-only in retain.
    assert any("snapshot" in a for a in delete_actions)
    assert delete_actions[-1] == {"delete": {}} or any("delete" in a for a in delete_actions)
    assert any("read_only" in a for a in states["retain"]["actions"])
