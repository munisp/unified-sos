"""Wazuh SIEM binding for security-relevant CAD events (fail-closed seam).

Forwards security-relevant CAD events — dispatch creation, ratification-gate
transitions/denials, arms-register attempts, stream sessions — to the Wazuh
SIEM for central security monitoring. Selection idiom mirrors
mod-erp-bridge ``build_adapter``:

* Default (``SOS_CAD_PROFILE`` unset / ``fixture``/``local``/``test``) is the
  deterministic :class:`FixtureWazuhAdapter` (in-memory, queryable in tests).
* ``SOS_CAD_PROFILE=production`` hard-fails AT BOOT with
  :class:`AdapterUnavailableError` unless ``SOS_WAZUH_URL`` and
  ``SOS_WAZUH_API_TOKEN`` are both configured.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Protocol

from .webrtc import AdapterUnavailableError


class WazuhAdapter(Protocol):
    """SIEM forwarding seam. ``event`` is a JSON-serialisable dict with at
    least ``event_type`` and ``tenant_state_id`` keys."""

    def forward_event(self, event: Dict) -> None: ...


class FixtureWazuhAdapter:
    """Deterministic in-memory SIEM sink for tests and local runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: List[Dict] = []

    def forward_event(self, event: Dict) -> None:
        with self._lock:
            self.events.append(dict(event))

    def by_type(self, event_type: str) -> List[Dict]:
        with self._lock:
            return [e for e in self.events if e.get("event_type") == event_type]


class HttpWazuhAdapter:
    """Production seam: POSTs events to the Wazuh indexer/manager API.

    Fail-closed: construction requires both ``SOS_WAZUH_URL`` and
    ``SOS_WAZUH_API_TOKEN``; any HTTP failure at call time raises
    :class:`AdapterUnavailableError` — a CAD security event must never be
    silently dropped in production.
    """

    def __init__(self, url: str, api_token: str) -> None:
        missing = []
        if not url:
            missing.append("SOS_WAZUH_URL")
        if not api_token:
            missing.append("SOS_WAZUH_API_TOKEN")
        if missing:
            raise AdapterUnavailableError(
                f"Wazuh SIEM adapter selected but {', '.join(missing)} not "
                "configured (fail-closed)"
            )
        self._url = url.rstrip("/")
        self._api_token = api_token

    def forward_event(self, event: Dict) -> None:
        import httpx

        try:
            resp = httpx.post(
                f"{self._url}/cad/events",
                json=event,
                headers={"Authorization": f"Bearer {self._api_token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise AdapterUnavailableError(
                f"Wazuh SIEM at {self._url} rejected/unreachable: {exc}"
            ) from exc


def build_wazuh_adapter(env: Optional[Dict[str, str]] = None) -> WazuhAdapter:
    """Select the Wazuh SIEM adapter from ``SOS_CAD_PROFILE``.

    Default ``fixture``/unset is the deterministic in-memory adapter.
    ``production`` fails closed AT BOOT unless ``SOS_WAZUH_URL`` and
    ``SOS_WAZUH_API_TOKEN`` are configured.
    """
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_CAD_PROFILE", "fixture")
    if profile in ("fixture", "local", "test"):
        return FixtureWazuhAdapter()
    if profile in ("production", "live"):
        return HttpWazuhAdapter(
            url=env.get("SOS_WAZUH_URL", ""),
            api_token=env.get("SOS_WAZUH_API_TOKEN", ""),
        )
    raise AdapterUnavailableError(
        f"unknown SOS_CAD_PROFILE {profile!r}; expected fixture|production"
    )
