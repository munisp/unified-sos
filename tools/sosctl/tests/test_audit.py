"""Tests for `sosctl audit verify-chain` (P1 workstream 4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sosctl import audit
from sosctl.cli import app

runner = CliRunner()

#: services/ dir (tools/sosctl/tests -> ../../../services)
SERVICES_ROOT = Path(__file__).resolve().parents[3] / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))
from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash  # noqa: E402


def _write_chain(root: Path, tenant: str, n: int = 3) -> list[dict]:
    """Write a valid n-event chain (genesis + events) as JSONL."""
    events = []
    prev = GENESIS_PREV_HASH
    for i in range(n):
        payload = {
            "seq": i + 1,
            "event_id": f"evt-{i + 1:06d}",
            "event_type": "ng.sos.audit.genesis" if i == 0 else "ng.sos.tenant.provisioned",
            "tenant_id": tenant,
            "actor": "control-plane",
            "detail": {"i": i},
            "at": "2026-01-01T00:00:00+00:00",
        }
        event = {**payload, "prev_hash": prev,
                 "event_hash": event_payload_hash(payload, prev)}
        prev = event["event_hash"]
        events.append(event)
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"{tenant}.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return events


def test_verify_chain_intact(tmp_path) -> None:
    _write_chain(tmp_path, "tn-ok")
    result = runner.invoke(app, ["audit", "verify-chain", "tn-ok",
                                 "--archive-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "intact" in result.output


def test_verify_chain_tamper_exits_nonzero(tmp_path) -> None:
    events = _write_chain(tmp_path, "tn-evil")
    events[1]["actor"] = "mallory"  # mutate one archived event
    with (tmp_path / "tn-evil.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    result = runner.invoke(app, ["audit", "verify-chain", "tn-evil",
                                 "--archive-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "COMPROMISED" in result.output


def test_verify_chain_missing_archive(tmp_path) -> None:
    result = runner.invoke(app, ["audit", "verify-chain", "tn-ghost",
                                 "--archive-root", str(tmp_path)])
    assert result.exit_code == 2


def test_load_tenant_chain_roundtrip(tmp_path) -> None:
    written = _write_chain(tmp_path, "tn-rt")
    assert audit.load_tenant_chain(tmp_path, "tn-rt") == written
    assert audit.verify_tenant_chain(tmp_path, "tn-rt") == []
