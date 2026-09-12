"""Pytest plumbing for mod-land-docs (mirrors mod-safecity-vision idioms)."""

from __future__ import annotations

import sys
from pathlib import Path

# services/ dir on sys.path for the _shared package (same idiom as app/main.py).
_SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))
# service dir on sys.path for the landdocs_app package
_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

import pytest
from fastapi.testclient import TestClient

from _shared.eventbus import InMemoryEventBus
from landdocs_app.main import create_app


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def client(bus: InMemoryEventBus) -> TestClient:
    return TestClient(create_app(bus=bus))
