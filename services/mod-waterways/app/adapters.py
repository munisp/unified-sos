"""Fail-closed adapter bindings for mod-waterways (idiom mirrors mod-erp-bridge
/ mod-police-cad ``build_*`` factories).

* :class:`SedonaVolumetricsAdapter` — satellite/bathymetric volumetric
  verification of dredging surveys. The fixture computes volume
  deterministically as polygon area (shoelace, equirectangular-scaled) ×
  depth; the live seam POSTs to ``SOS_WATERWAYS_SEDONA_URL``.
* :class:`VesselAisAdapter` — AIS position telemetry for dredgers/ferries.
  Fixture generates deterministic positions from the vessel MMSI hash; the
  live seam queries ``SOS_WATERWAYS_AIS_URL``.

Selection is via ``SOS_WATERWAYS_PROFILE``: unset / ``fixture`` / ``local`` /
``test`` (default) → deterministic fixtures; ``production`` / ``live``
hard-fails AT BOOT with :class:`AdapterUnavailableError` without config.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol


class AdapterUnavailableError(RuntimeError):
    """Raised when a production adapter is selected without the configuration
    required to reach its backend (fail-closed — never degrade silently)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Reference fixture depth for volumetric verification (metres).
FIXTURE_DEPTH_M = 3.0


@dataclass(frozen=True)
class VolumetricVerification:
    """Cross-check of a dredging survey's claimed volume."""

    polygon_area_m2: float
    depth_m: float
    verified_volume_m3: float


@dataclass(frozen=True)
class AisPosition:
    """One AIS position report for a vessel."""

    mmsi: str
    latitude: float
    longitude: float
    speed_knots: float
    reported_at: str


def _polygon_area_m2(polygon: List[List[float]]) -> float:
    """Shoelace area over [lon, lat] vertices, equirectangular-scaled to m²
    (mirrors app.domain.polygon_area_m2; duplicated so adapters stay
    importable without the domain package in minimal images)."""
    import math

    ring = polygon[:-1] if len(polygon) >= 4 and polygon[0] == polygon[-1] else polygon
    n = len(ring)
    mean_lat = math.radians(sum(p[1] for p in ring) / n)
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(mean_lat)
    pts = [(p[0] * km_per_deg_lon, p[1] * km_per_deg_lat) for p in ring]
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0 * 1_000_000.0


class SedonaVolumetricsAdapter(Protocol):
    """Volumetric verification seam (Apache Sedona / PostGIS in production)."""

    def verify_volume(self, polygon: List[List[float]], depth_m: float) -> VolumetricVerification: ...


class FixtureSedonaAdapter:
    """Deterministic fixture: verified volume = polygon area × depth.

    No network, no clock — the same polygon + depth always yields the same
    verified volume, which keeps royalty/alert tests deterministic.
    """

    def verify_volume(self, polygon: List[List[float]], depth_m: float) -> VolumetricVerification:
        if len(polygon) < 3:
            raise ValueError("polygon must have at least 3 vertices")
        if depth_m <= 0:
            raise ValueError("depth_m must be positive")
        area = _polygon_area_m2(polygon)
        return VolumetricVerification(
            polygon_area_m2=round(area, 3),
            depth_m=depth_m,
            verified_volume_m3=round(area * depth_m, 3),
        )


class HttpSedonaAdapter:
    """Production seam: delegates volumetric computation to the Sedona
    cluster endpoint. Fail-closed at boot without ``SOS_WATERWAYS_SEDONA_URL``
    and at call time on any backend error."""

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise AdapterUnavailableError(
                "SOS_WATERWAYS_SEDONA_URL not configured; Sedona volumetrics "
                "unavailable (fail-closed)"
            )
        self._base_url = base_url.rstrip("/")

    def verify_volume(self, polygon: List[List[float]], depth_m: float) -> VolumetricVerification:
        try:
            import httpx

            resp = httpx.post(
                f"{self._base_url}/volumetrics/verify",
                json={"polygon": polygon, "depth_m": depth_m},
                timeout=15.0,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            raise AdapterUnavailableError(
                f"Sedona endpoint at {self._base_url} unreachable/rejected: {exc}"
            ) from exc
        return VolumetricVerification(
            polygon_area_m2=body["polygon_area_m2"],
            depth_m=body["depth_m"],
            verified_volume_m3=body["verified_volume_m3"],
        )


class VesselAisAdapter(Protocol):
    """AIS telemetry seam for dredgers/ferries."""

    def latest_position(self, mmsi: str) -> AisPosition: ...


class FixtureAisAdapter:
    """Deterministic fixture AIS source: positions derived from an MMSI hash
    inside Nigeria's coastal/inland bounding region — same MMSI, same fix."""

    def latest_position(self, mmsi: str) -> AisPosition:
        if not mmsi or not mmsi.isdigit():
            raise ValueError("mmsi must be a numeric identifier")
        digest = hashlib.sha256(mmsi.encode("utf-8")).digest()
        lat = 4.5 + (digest[0] / 255.0) * 7.0   # 4.5–11.5 N
        lon = 3.0 + (digest[1] / 255.0) * 10.0  # 3.0–13.0 E
        speed = round((digest[2] / 255.0) * 12.0, 1)
        return AisPosition(mmsi=mmsi, latitude=round(lat, 5),
                           longitude=round(lon, 5), speed_knots=speed,
                           reported_at="1970-01-01T00:00:00+00:00")


class HttpAisAdapter:
    """Production seam: queries the AIS aggregator. Fail-closed at boot
    without ``SOS_WATERWAYS_AIS_URL`` and at call time on backend errors."""

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise AdapterUnavailableError(
                "SOS_WATERWAYS_AIS_URL not configured; AIS telemetry "
                "unavailable (fail-closed)"
            )
        self._base_url = base_url.rstrip("/")

    def latest_position(self, mmsi: str) -> AisPosition:
        try:
            import httpx

            resp = httpx.get(f"{self._base_url}/vessels/{mmsi}/position", timeout=10.0)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            raise AdapterUnavailableError(
                f"AIS endpoint at {self._base_url} unreachable/rejected: {exc}"
            ) from exc
        return AisPosition(mmsi=mmsi, latitude=body["latitude"],
                           longitude=body["longitude"],
                           speed_knots=body["speed_knots"],
                           reported_at=body.get("reported_at", _now()))


def build_sedona_adapter(env: Optional[Dict[str, str]] = None) -> SedonaVolumetricsAdapter:
    """Select the volumetrics adapter from ``SOS_WATERWAYS_PROFILE``.

    Default is the deterministic fixture; ``production``/``live`` fails closed
    AT BOOT without ``SOS_WATERWAYS_SEDONA_URL``.
    """
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_WATERWAYS_PROFILE", "fixture")
    if profile in ("fixture", "local", "test"):
        return FixtureSedonaAdapter()
    if profile in ("production", "live"):
        return HttpSedonaAdapter(env.get("SOS_WATERWAYS_SEDONA_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_WATERWAYS_PROFILE {profile!r}; expected fixture|production"
    )


def build_ais_adapter(env: Optional[Dict[str, str]] = None) -> VesselAisAdapter:
    """Select the AIS adapter from ``SOS_WATERWAYS_PROFILE`` (fail-closed)."""
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_WATERWAYS_PROFILE", "fixture")
    if profile in ("fixture", "local", "test"):
        return FixtureAisAdapter()
    if profile in ("production", "live"):
        return HttpAisAdapter(env.get("SOS_WATERWAYS_AIS_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_WATERWAYS_PROFILE {profile!r}; expected fixture|production"
    )
