"""Shared fixtures for sosctl tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: Repo root (tools/sosctl/tests -> ../../..)
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "policy-packs" / "revenue-split.schema.json"


def valid_pack(state: str = "ogun") -> dict:
    """A policy pack that passes schema + all guardrails."""
    return {
        "tenant_state_id": state,
        "policy_id": "POL_OGUN_REV_SPLIT_2026",
        "revenue_head": "REV_LUC",
        "effective_date": "2026-07-01",
        "gazette_reference": "OGSL-GAZETTE-2026-11",
        "statutory_split_rules": [
            {
                "beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND",
                "tigerbeetle_account_code": 3001,
                "split_percentage": 80.0,
                "deduction_timing": "INSTANT",
            },
            {
                "beneficiary": "MDA_RETENTION_ACCOUNT",
                "tigerbeetle_account_code": 2010,
                "split_percentage": 12.0,
                "deduction_timing": "INSTANT",
            },
            {
                "beneficiary": "PPP_TECH_CONCESSIONAIRE_ESCROW",
                "tigerbeetle_account_code": 2099,
                "split_percentage": 8.0,
                "deduction_timing": "END_OF_MONTH",
            },
        ],
        "concession_guardrails": {"revenue_share_ceiling_pct": 8.0, "irr_cap_pct": 18.0},
    }


@pytest.fixture()
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture()
def pack_file(tmp_path: Path) -> Path:
    p = tmp_path / "policy-pack.json"
    p.write_text(json.dumps(valid_pack()))
    return p


@pytest.fixture()
def write_pack(tmp_path: Path):
    def _write(doc: dict, name: str = "policy-pack.json") -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(doc))
        return p

    return _write
