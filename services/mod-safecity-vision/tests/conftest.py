"""Pytest plumbing for mod-safecity-vision (mirrors mod-police-cad idioms)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# services/ dir on sys.path for the _shared package (same idiom as app/main.py).
_SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))

import pytest
from fastapi.testclient import TestClient

from _shared.eventbus import InMemoryEventBus
from app.gate import AuthorizationGate, AuthorizationRecord
from app.main import create_app


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")


def make_gate(*records: AuthorizationRecord) -> AuthorizationGate:
    return AuthorizationGate(records=tuple(records))


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def client(bus: InMemoryEventBus) -> TestClient:
    """Default build: gate CLOSED (no certified authorization source)."""
    return TestClient(create_app(gate=AuthorizationGate(), bus=bus))


@pytest.fixture()
def authorized_client(bus: InMemoryEventBus) -> TestClient:
    """Build with a certified, unexpired warrant for tenant 'lagos'."""
    gate = make_gate(
        AuthorizationRecord(
            ref="WRT-LAG-2026-0142", kind="warrant",
            tenant_state_id="lagos", expires_at=_future(),
        )
    )
    return TestClient(create_app(gate=gate, bus=bus))


@pytest.fixture()
def authorizations_file(tmp_path) -> str:
    """A certified authorization source JSON file (for from_env coverage)."""
    path = tmp_path / "authorizations.json"
    path.write_text(json.dumps([{
        "ref": "DPO-OGN-2026-0007", "kind": "dpo_approval",
        "tenant_state_id": "ogun", "expires_at": _future(),
    }]))
    return str(path)
