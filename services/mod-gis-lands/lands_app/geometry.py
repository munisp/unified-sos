"""Geometry validation & measurement for cadastral parcels.

Mirrors the PostGIS semantics of db/migrations/0001_cadastre.sql:

* ``boundary_geom GEOMETRY(Polygon, 4326)``  -> we accept only valid GeoJSON
  Polygon geometries whose coordinates sit inside WGS84 lon/lat bounds.
* Trigger ``trg_parcels_no_overlap`` (ST_Intersects AND NOT ST_Touches against
  active titles of the same tenant) -> :func:`find_overlap` / :class:`OverlapError`.
  Acceptance gate M4.2 is *zero overlapping-polygon tolerance at sub-meter
  precision*, so the default overlap tolerance is 0.01 m² (1 dm²): shared
  beacon points / shared edges (``ST_Touches``) are legal, anything with real
  interior overlap area is rejected.

All linear/area metrics are computed geodesically via pyproj (WGS84 ellipsoid),
not in raw degrees, so sub-meter claims are meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyproj import Geod
from shapely import validation
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

#: WGS84 geodetic calculator for ellipsoidal area/perimeter.
_GEOD = Geod(ellps="WGS84")

#: Rejection threshold for interior overlap between two parcels, in square
#: metres. M4.2 says "zero overlapping-polygon tolerance, sub-meter precision";
#: 0.01 m² swallows floating-point noise while any real encroachment fails.
DEFAULT_OVERLAP_TOLERANCE_M2 = 0.01

#: Accepted relative deviation between declared area_sqm and the geodesically
#: computed boundary area. Survey plans carry rounded areas; 5% is generous
#: enough for rounding while still catching swapped/duplicated boundaries.
DECLARED_AREA_REL_TOLERANCE = 0.05


class GeometryError(ValueError):
    """Raised when a submitted boundary is not a usable EPSG:4326 polygon."""


@dataclass(frozen=True)
class OverlapError(Exception):  # noqa: N818 - domain error, not a bug signal
    """Raised when a candidate parcel overlaps an existing active title."""

    conflicting_parcel_uin: str
    overlap_area_m2: float

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Boundary overlaps existing title {self.conflicting_parcel_uin} "
            f"by {self.overlap_area_m2:.4f} m²"
        )


def parse_boundary(boundary_geojson: dict) -> BaseGeometry:
    """Parse + validate a GeoJSON Polygon boundary.

    Enforces the PostGIS column contract ``GEOMETRY(Polygon, 4326)``:
    GeoJSON (RFC 7946) is WGS84 by definition; we additionally reject
    coordinates outside lon/lat bounds (a common symptom of submitting UTM
    Minna Datum beacon coordinates — EPSG:26391/26392/26393 — without
    reprojection).
    """

    if not isinstance(boundary_geojson, dict):
        raise GeometryError("boundary_geojson must be a GeoJSON object")
    if boundary_geojson.get("type") != "Polygon":
        raise GeometryError(
            f"boundary_geojson must be a GeoJSON Polygon, got {boundary_geojson.get('type')!r}"
        )
    # Explicit CRS, if present, must be WGS84 (RFC 7946 defaults to EPSG:4326).
    crs = boundary_geojson.get("crs")
    if crs is not None and "4326" not in str(crs) and "CRS84" not in str(crs).upper():
        raise GeometryError(f"boundary CRS must be EPSG:4326/WGS84, got {crs!r}")

    try:
        geom = shape(boundary_geojson)
    except Exception as exc:  # shapely raises several error types
        raise GeometryError(f"unparseable GeoJSON polygon: {exc}") from exc

    if geom.is_empty:
        raise GeometryError("boundary polygon is empty")
    if not geom.is_valid:
        raise GeometryError(
            f"boundary polygon is invalid: {validation.explain_validity(geom)}"
        )

    minx, miny, maxx, maxy = geom.bounds
    if not (-180.0 <= minx <= maxx <= 180.0 and -90.0 <= miny <= maxy <= 90.0):
        raise GeometryError(
            "coordinates outside WGS84 lon/lat bounds — reproject UTM/Minna Datum "
            "beacons (EPSG:26391-26393) to EPSG:4326 before submission"
        )
    return geom


def area_sqm(geom: BaseGeometry) -> float:
    """Geodesic area of a polygon in square metres (WGS84 ellipsoid)."""

    area, _ = _GEOD.geometry_area_perimeter(geom)
    return abs(area)


def intersection_area_sqm(a: BaseGeometry, b: BaseGeometry) -> float:
    """Geodesic area of the (possibly empty) intersection of two polygons."""

    inter = a.intersection(b)
    if inter.is_empty:
        return 0.0
    # Intersection of polygons can include line/point slivers with zero area.
    if inter.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
        return 0.0
    return area_sqm(inter)


def check_declared_area(geom: BaseGeometry, declared_area_sqm: float) -> None:
    """Cross-check the survey-plan declared area against the boundary polygon."""

    computed = area_sqm(geom)
    if computed <= 0:
        raise GeometryError("boundary polygon has zero geodesic area")
    if abs(computed - declared_area_sqm) / computed > DECLARED_AREA_REL_TOLERANCE:
        raise GeometryError(
            f"declared area_sqm {declared_area_sqm:.2f} deviates from boundary "
            f"geodesic area {computed:.2f} m² by more than "
            f"{DECLARED_AREA_REL_TOLERANCE:.0%}"
        )


def find_overlap(
    candidate: BaseGeometry,
    existing: list[tuple[str, BaseGeometry]],
    tolerance_m2: float = DEFAULT_OVERLAP_TOLERANCE_M2,
) -> OverlapError | None:
    """Return an OverlapError for the first conflicting existing parcel, else None.

    Semantics mirror the PL/pgSQL trigger ``cadastre.reject_overlapping_parcel``:
    a conflict requires interior intersection (``ST_Intersects`` without
    ``ST_Touches``) *and* an overlap area beyond the sub-meter tolerance.

    :param existing: (parcel_uin, geometry) pairs of ACTIVE parcels **of the
        same tenant state** (tenancy scoping is done by the repository, exactly
        like the RLS policy + trigger's ``tenant_state_id = NEW.tenant_state_id``).
    """

    for uin, geom in existing:
        if not candidate.intersects(geom):
            continue
        if candidate.touches(geom) and not candidate.crosses(geom):
            # Shared beacon/edge only — allowed (NOT ST_Touches in the trigger).
            # Crosses() with polygons implies interior intersection, so only
            # pure touches short-circuit here.
            if intersection_area_sqm(candidate, geom) <= tolerance_m2:
                continue
        overlap = intersection_area_sqm(candidate, geom)
        if overlap > tolerance_m2:
            return OverlapError(conflicting_parcel_uin=uin, overlap_area_m2=overlap)
    return None
