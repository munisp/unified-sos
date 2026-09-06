"""Stage 7.C wiring test: instrument_fastapi is attached to the app."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.main import create_app


def test_metrics_endpoint_wired():
    client = TestClient(create_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert 'service_info{service="mod-identity"' in resp.text
