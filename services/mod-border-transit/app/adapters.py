"""Fail-closed hardware/telematics adapter seams for mod-border-transit.

Selection idiom mirrors mod-police-cad ``build_webrtc_gateway`` and
mod-erp-bridge ``build_adapter``:

* Default (``SOS_BORDER_PROFILE`` unset / ``fixture`` / ``local`` / ``test``)
  is the deterministic fixture adapter — no network, canned scans/pings.
* ``SOS_BORDER_PROFILE=production`` hard-fails AT BOOT with
  :class:`AdapterUnavailableError` unless the live seam URL is configured
  (``SOS_BORDER_RFID_URL`` / ``SOS_BORDER_TELEMATICS_URL``).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Protocol


class AdapterUnavailableError(RuntimeError):
    """Raised when a production adapter is selected without the configuration
    required to reach the live reader/telematics backend."""


class RfidReaderAdapter(Protocol):
    """RFID checkpoint reader seam: pulls pending scan events for a tenant."""

    def fetch_scans(self, tenant_state_id: str) -> List[dict]: ...


class TelematicsAdapter(Protocol):
    """Fleet telematics seam: pulls pending GPS pings for a tenant."""

    def fetch_pings(self, tenant_state_id: str) -> List[dict]: ...


class FixtureRfidReaderAdapter:
    """Deterministic in-memory RFID reader for tests and local runs.

    Every call returns the same canned scan batch (per tenant), so fixture
    determinism can be asserted by tests.
    """

    def fetch_scans(self, tenant_state_id: str) -> List[dict]:
        return [
            {
                "rfid_tag_id": f"RFID-{tenant_state_id.upper()}-0001",
                "checkpoint_id": "fixture-checkpoint",
                "direction": "entry",
                "scanned_at": "2025-01-01T08:00:00+00:00",
                "latitude": 0.0,
                "longitude": 0.0,
                "seal_intact": True,
            }
        ]


class LiveRfidReaderAdapter:
    """Production seam over HTTP. Fail-closed: any reader error raises
    :class:`AdapterUnavailableError` rather than degrading silently."""

    def __init__(self, reader_url: str) -> None:
        if not reader_url:
            raise AdapterUnavailableError(
                "SOS_BORDER_RFID_URL not configured; RFID reader unavailable "
                "(fail-closed)"
            )
        self._reader_url = reader_url.rstrip("/")

    def fetch_scans(self, tenant_state_id: str) -> List[dict]:
        try:
            import httpx

            resp = httpx.get(
                f"{self._reader_url}/scans",
                params={"tenant_state_id": tenant_state_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()["scans"]
        except Exception as exc:  # fail closed on any reader error
            raise AdapterUnavailableError(
                f"RFID reader at {self._reader_url} unreachable/rejected: {exc}"
            ) from exc


class FixtureTelematicsAdapter:
    """Deterministic in-memory telematics feed for tests and local runs."""

    def fetch_pings(self, tenant_state_id: str) -> List[dict]:
        return [
            {
                "truck_id": f"TRK-{tenant_state_id.upper()}-0001",
                "latitude": 6.9,
                "longitude": 2.8,
                "speed_kph": 62.0,
                "pinged_at": "2025-01-01T08:05:00+00:00",
            }
        ]


class LiveTelematicsAdapter:
    """Production seam over HTTP. Fail-closed on construction and at call."""

    def __init__(self, telematics_url: str) -> None:
        if not telematics_url:
            raise AdapterUnavailableError(
                "SOS_BORDER_TELEMATICS_URL not configured; telematics backend "
                "unavailable (fail-closed)"
            )
        self._telematics_url = telematics_url.rstrip("/")

    def fetch_pings(self, tenant_state_id: str) -> List[dict]:
        try:
            import httpx

            resp = httpx.get(
                f"{self._telematics_url}/pings",
                params={"tenant_state_id": tenant_state_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()["pings"]
        except Exception as exc:  # fail closed on any backend error
            raise AdapterUnavailableError(
                f"telematics backend at {self._telematics_url} "
                f"unreachable/rejected: {exc}"
            ) from exc


def build_rfid_adapter(env: Optional[Dict[str, str]] = None) -> RfidReaderAdapter:
    """Select the RFID reader adapter from ``SOS_BORDER_PROFILE``.

    Default ``fixture``/unset is the deterministic in-memory reader.
    ``production`` fails closed AT BOOT when ``SOS_BORDER_RFID_URL`` is unset.
    """
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_BORDER_PROFILE", "fixture")
    if profile in ("fixture", "local", "test"):
        return FixtureRfidReaderAdapter()
    if profile in ("production", "live"):
        return LiveRfidReaderAdapter(env.get("SOS_BORDER_RFID_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_BORDER_PROFILE {profile!r}; expected fixture|production"
    )


def build_telematics_adapter(
    env: Optional[Dict[str, str]] = None,
) -> TelematicsAdapter:
    """Select the telematics adapter from ``SOS_BORDER_PROFILE`` (fail-closed)."""
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_BORDER_PROFILE", "fixture")
    if profile in ("fixture", "local", "test"):
        return FixtureTelematicsAdapter()
    if profile in ("production", "live"):
        return LiveTelematicsAdapter(env.get("SOS_BORDER_TELEMATICS_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_BORDER_PROFILE {profile!r}; expected fixture|production"
    )
