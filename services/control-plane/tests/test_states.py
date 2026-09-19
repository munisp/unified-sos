"""National Edition provisioning: all 36 states + FCT are valid tenants."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import VALID_STATES
from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_valid_states_matches_config_states_pack_list():
    """VALID_STATES must mirror config/states/validate_packs.py STATES (37)."""
    from pathlib import Path
    import re

    src = (Path(__file__).resolve().parents[3]
           / "config" / "states" / "validate_packs.py").read_text()
    block = re.search(r"STATES = \[(.*?)\]", src, re.S).group(1)
    canonical = re.findall(r'"([a-z_]+)"', block)
    assert sorted(VALID_STATES) == sorted(canonical)
    assert len(VALID_STATES) == 37


@pytest.mark.parametrize("state", sorted(VALID_STATES))
def test_provisioning_accepts_state(client: TestClient, state: str) -> None:
    resp = client.post("/control/v1/tenants",
                       json={"state": state, "tier": "shared"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] in ("active", "provisioning")


def test_provisioning_rejects_non_state(client: TestClient) -> None:
    resp = client.post("/control/v1/tenants",
                       json={"state": "wakanda", "tier": "shared"})
    assert resp.status_code == 422


def test_tier_regex_preserved(client: TestClient) -> None:
    resp = client.post("/control/v1/tenants",
                       json={"state": "fct", "tier": "platinum"})
    assert resp.status_code == 422
