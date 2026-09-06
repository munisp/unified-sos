"""Shared fixtures/helpers: synthetic EPSG:4326 parcel boundaries near Abeokuta, Ogun."""

from __future__ import annotations

from pyproj import Geod

_GEOD = Geod(ellps="WGS84")

# Abeokuta, Ogun State — plausible survey coordinates (WGS84).
BASE_LON, BASE_LAT = 3.3500, 7.1500


def square_geojson(lon0: float, lat0: float, size_deg: float = 0.001) -> dict:
    """An axis-aligned GeoJSON Polygon square of ``size_deg`` degrees."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon0, lat0],
            [lon0 + size_deg, lat0],
            [lon0 + size_deg, lat0 + size_deg],
            [lon0, lat0 + size_deg],
            [lon0, lat0],
        ]],
    }


def geodesic_area_sqm(geojson: dict) -> float:
    """True WGS84 geodesic area — used to keep declared areas honest in tests."""
    from shapely.geometry import shape

    area, _ = _GEOD.geometry_area_perimeter(shape(geojson))
    return abs(area)


def registration_body(parcel_uin: str, geojson: dict, **overrides) -> dict:
    """A contract-valid ParcelRegistration body for the given boundary."""
    body = {
        "lga_id": "abeokuta-south",
        "parcel_uin": parcel_uin,
        "owner_stin": "STIN-OG-0001234",
        "land_use_type": "RESIDENTIAL",
        "survey_plan_no": f"OG/SVY/{parcel_uin}",
        "beacon_count": 4,
        "area_sqm": round(geodesic_area_sqm(geojson), 2),
        "boundary_geojson": geojson,
    }
    body.update(overrides)
    return body
