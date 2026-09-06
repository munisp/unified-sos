"""H3 indexing adapter.

Production mode uses the real ``h3`` package (Uber H3 hexagonal index).
Local/test mode falls back to ``LocalH3Adapter`` — a *clearly labeled*
deterministic geohash-like cell scheme (``LOCAL-GH<res>-<base32>``). The
local cells are stable and hierarchical but are **not** real H3 cells; the
backend name is exposed in every API response so callers can distinguish.
"""

from __future__ import annotations

from typing import Any

from ..domain import AdapterUnavailableError

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"  # geohash alphabet


def _geohash_encode(lat: float, lon: float, precision: int) -> str:
    lat_interval, lon_interval = [-90.0, 90.0], [-180.0, 180.0]
    geohash: list[str] = []
    bit, ch, even = 0, 0, True
    bits = (16, 8, 4, 2, 1)
    while len(geohash) < precision:
        if even:
            mid = (lon_interval[0] + lon_interval[1]) / 2
            if lon >= mid:
                ch |= bits[bit]
                lon_interval[0] = mid
            else:
                lon_interval[1] = mid
        else:
            mid = (lat_interval[0] + lat_interval[1]) / 2
            if lat >= mid:
                ch |= bits[bit]
                lat_interval[0] = mid
            else:
                lat_interval[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            geohash.append(_B32[ch])
            bit, ch = 0, 0
    return "".join(geohash)


def _geometry_points(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    """Representative positions for a GeoJSON geometry (exterior vertices +
    centroid-ish midpoint) — enough for deterministic local cell coverage."""

    points: list[tuple[float, float]] = []

    def walk(coords: Any) -> None:
        if isinstance(coords, (list, tuple)):
            if len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
                points.append((float(coords[1]), float(coords[0])))  # (lat, lon)
                return
            for item in coords:
                walk(item)

    walk(geometry.get("coordinates"))
    return points


class LocalH3Adapter:
    """Deterministic LOCAL fallback — geohash-like cells, NOT real H3.

    Clearly labeled via ``backend_name == "local-geohash-fallback"`` and the
    ``LOCAL-GH`` cell prefix. Used in local/test mode or whenever the real
    ``h3`` package is unavailable outside production.
    """

    backend_name = "local-geohash-fallback"

    def cell_for_point(self, lat: float, lon: float, resolution: int) -> str:
        # Map H3-ish resolution 0..15 onto geohash precision 1..8.
        precision = max(1, min(8, (resolution // 2) + 1))
        return f"LOCAL-GH{resolution}-{_geohash_encode(lat, lon, precision)}"

    def cells_for_geometry(self, geometry: dict, resolution: int) -> list[str]:
        cells = {self.cell_for_point(lat, lon, resolution) for lat, lon in _geometry_points(geometry)}
        return sorted(cells)


class RealH3Adapter:
    """Production adapter backed by the optional ``h3`` package."""

    backend_name = "h3"

    def __init__(self) -> None:
        try:
            import h3  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError("RealH3Adapter requires the optional 'h3' package") from exc

    def cell_for_point(self, lat: float, lon: float, resolution: int) -> str:  # pragma: no cover
        import h3

        return h3.latlng_to_cell(lat, lon, resolution)

    def cells_for_geometry(self, geometry: dict, resolution: int) -> list[str]:  # pragma: no cover
        import h3

        return sorted(h3.geo_to_cells(geometry, resolution))


def get_h3_adapter(prefer_real: bool = False) -> LocalH3Adapter | RealH3Adapter:
    """Factory: real h3 when requested and installed, otherwise the labeled
    deterministic local fallback. In production mode, if h3 is requested the
    seam fails closed rather than silently degrading."""

    if prefer_real:
        from .base import is_production

        try:
            return RealH3Adapter()
        except AdapterUnavailableError:
            if is_production():
                raise
    return LocalH3Adapter()
