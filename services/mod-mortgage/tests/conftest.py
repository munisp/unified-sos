"""Pytest plumbing for mod-mortgage (mirrors mod-safecity-vision idioms)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# service dir + services/ dir on sys.path (same idiom as mortgage_app/main.py).
_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _SERVICE_ROOT.parent
for p in (str(_SERVICE_ROOT), str(_SERVICES_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest
from fastapi.testclient import TestClient

from _shared.eventbus import InMemoryEventBus
from mortgage_app.adapters import FixtureLedgerAdapter
from mortgage_app.domain import MortgageStore
from mortgage_app.main import create_app

TENANT = "lagos"
HEADERS = {"X-State-Tenant": TENANT}
BASE = f"/api/v1/states/{TENANT}/mortgages"


class FixedScorer:
    """Deterministic test scorer returning a fixed score."""

    def __init__(self, score: int) -> None:
        self._score = score

    def score(self, applicant_id: str) -> int:
        return self._score


def make_now(y=2026, m=1, d=1) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def ledger() -> FixtureLedgerAdapter:
    return FixtureLedgerAdapter()


@pytest.fixture()
def make_store(ledger):
    """Factory: MortgageStore with a fixed credit score and mutable clock."""
    def _make(score: int = 700, clock=None) -> MortgageStore:
        return MortgageStore(scorer=FixedScorer(score), ledger=ledger,
                             clock=clock or (lambda: make_now()))
    return _make


@pytest.fixture()
def client(bus, make_store) -> TestClient:
    return TestClient(create_app(store=make_store(), bus=bus))


def apply_payload(**over):
    body = {
        "applicant_id": "applicant-001",
        "parcel_id": "parcel-001",
        "title_ref": "LAG-2024-000123",
        "principal_kobo": 600_000,
        "rate_bps": 1200,  # 12% p.a. = 1%/month
        "term_months": 6,
    }
    body.update(over)
    return body


def run_to_approved(client: TestClient, **over) -> str:
    r = client.post(BASE + "/", json=apply_payload(**over), headers=HEADERS)
    assert r.status_code == 201, r.text
    mid = r.json()["mortgage"]["mortgage_id"]
    r = client.post(f"{BASE}/{mid}/credit-review", headers=HEADERS)
    assert r.status_code == 200, r.text
    return mid


def run_to_lien(client: TestClient, **over) -> str:
    mid = run_to_approved(client, **over)
    r = client.post(f"{BASE}/{mid}/register-lien", json={}, headers=HEADERS)
    assert r.status_code == 201, r.text
    return mid


def run_to_disbursed(client: TestClient, **over) -> str:
    mid = run_to_lien(client, **over)
    r = client.post(f"{BASE}/{mid}/disburse", headers=HEADERS)
    assert r.status_code == 200, r.text
    return mid
