"""Geometry helpers: GeoJSON validation, hashing, and deterministic digests.

Validation is intentionally dependency-light: structural checks are pure
Python; shapely is used for area/validity when available (it is a hard
requirement of this service, matching mod-gis-lands).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry


class GeometryError(ValueError):
    pass


_ALLOWED_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def _iter_positions(coords: Any) -> Iterable[tuple[float, float]]:
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and all(isinstance(c, (int, float)) for c in coords[:2]):
            yield (float(coords[0]), float(coords[1]))
            return
        for item in coords:
            yield from _iter_positions(item)


def parse_geojson_geometry(geometry: dict[str, Any]) -> BaseGeometry:
    """Validate a GeoJSON geometry dict and return a shapely geometry.

    Checks: supported type, coordinate ranges (-180..180, -90..90), polygon
    ring closure and minimum ring size, and shapely validity.
    """

    if not isinstance(geometry, dict):
        raise GeometryError("geometry must be a GeoJSON object")
    gtype = geometry.get("type")
    if gtype not in _ALLOWED_TYPES:
        raise GeometryError(f"unsupported geometry type: {gtype!r}")
    coords = geometry.get("coordinates")
    if not coords:
        raise GeometryError("geometry has no coordinates")

    positions = list(_iter_positions(coords))
    if not positions:
        raise GeometryError("geometry has no positions")
    for lon, lat in positions:
        if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            raise GeometryError(f"coordinate out of range: ({lon}, {lat})")

    if gtype in ("Polygon", "MultiPolygon"):
        rings = coords if gtype == "Polygon" else [ring for poly in coords for ring in poly]
        for ring in rings:
            if len(ring) < 4:
                raise GeometryError("polygon ring must have at least 4 positions")
            if ring[0][:2] != ring[-1][:2]:
                raise GeometryError("polygon ring is not closed")

    geom = shape(geometry)
    if geom.is_empty:
        raise GeometryError("geometry is empty")
    if not geom.is_valid:
        raise GeometryError("geometry is not valid (self-intersection or broken ring)")
    return geom


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization used for all hashing."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def geometry_hash(geometry: dict[str, Any]) -> str:
    """Stable hash of a GeoJSON geometry (canonical coordinate ordering)."""

    return sha256_hex(canonical_json(geometry))


def feature_collection_from_parameters(features: list[dict[str, Any]]) -> list[tuple[dict[str, Any], BaseGeometry]]:
    """Parse a list of GeoJSON features (from job parameters) into
    ``(properties, geometry)`` pairs, validating each geometry."""

    out: list[tuple[dict[str, Any], BaseGeometry]] = []
    for feature in features:
        if feature.get("type") != "Feature":
            raise GeometryError("expected GeoJSON Feature objects")
        geom = parse_geojson_geometry(feature.get("geometry") or {})
        out.append((dict(feature.get("properties") or {}), geom))
    return out
