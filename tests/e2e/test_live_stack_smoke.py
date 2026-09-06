"""Live-stack smoke checks (Stage 7.B).

Skip-gated unless ``E2E_STACK=live``: these assert the integration compose
stack (tests/e2e/docker-compose.integration.yaml) is up and every journey
service answers its health endpoint.  The journey suites themselves switch
transport via the conftest fixtures when ``E2E_STACK=live``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e_live


@pytest.mark.parametrize(
    "env_var,path",
    [
        ("E2E_PORTAL_BASE_URL", "/healthz"),
        ("E2E_KYC_BASE_URL", "/healthz"),
        ("E2E_MARKET_BASE_URL", "/health"),
        ("E2E_TRANSPARENCY_BASE_URL", "/healthz"),
        ("E2E_CONTROL_PLANE_BASE_URL", "/healthz"),
    ],
)
def test_live_service_health(env_var: str, path: str):
    import os

    import httpx

    base = os.environ.get(env_var, "")
    if not base:
        pytest.fail(f"E2E_STACK=live requires {env_var} (fail-closed)")
    resp = httpx.get(base.rstrip("/") + path, timeout=10.0)
    assert resp.status_code == 200, resp.text
