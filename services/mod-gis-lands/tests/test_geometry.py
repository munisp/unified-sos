"""Geometry validation tests — mirror of the PostGIS column + trigger semantics."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.geometry import (
    GeometryError,
    area_sqm,
    check_declared_area,
    find_overlap,
    intersection_area_sqm,
    parse_boundary,
)
from tests.helpers import BASE_LAT, BASE_LON, geodesic_area_sqm, square_geojson


class TestParseBoundary:
    def test_valid_polygon_accepted(self):
        geom = parse_boundary(square_geojson(BASE_LON, BASE_LAT))
        assert geom.geom_type == "Polygon"
        assert geom.is_valid

    def test_non_polygon_rejected(self):
        with pytest.raises(GeometryError, match="Polygon"):
            parse_boundary({"type": "Point", "coordinates": [BASE_LON, BASE_LAT]})

    def test_self_intersecting_bowtie_rejected(self):
        bowtie = {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]],
        }
        with pytest.raises(GeometryError, match="invalid"):
            parse_boundary(bowtie)

    def test_utm_coordinates_rejected_as_out_of_bounds(self):
        # UTM Minna Datum (EPSG:26391) metres submitted unprojected.
        utm = {
            "type": "Polygon",
            "coordinates": [[[500000.0, 790000.0], [500100.0, 790000.0],
                             [500100.0, 790100.0], [500000.0, 790100.0], [500000.0, 790000.0]]],
        }
        with pytest.raises(GeometryError, match="reproject"):
            parse_boundary(utm)

    def test_non_wgs84_crs_rejected(self):
        geojson = square_geojson(BASE_LON, BASE_LAT)
        geojson["crs"] = {"type": "name", "properties": {"name": "EPSG:26391"}}
        with pytest.raises(GeometryError, match="CRS"):
            parse_boundary(geojson)


class TestArea:
    def test_geodesic_area_is_sub_meter_plausible(self):
        geojson = square_geojson(BASE_LON, BASE_LAT, 0.001)  # ~111 m per side
        computed = area_sqm(parse_boundary(geojson))
        assert abs(computed - geodesic_area_sqm(geojson)) < 1e-6
        assert 10_000 < computed < 14_000  # ~0.001° ≈ 110.6 m at the equator

    def test_declared_area_mismatch_rejected(self):
        geom = parse_boundary(square_geojson(BASE_LON, BASE_LAT))
        check_declared_area(geom, area_sqm(geom) * 1.02)  # 2% rounding OK
        with pytest.raises(GeometryError, match="deviates"):
            check_declared_area(geom, area_sqm(geom) * 2.0)


class TestOverlap:
    def test_touching_boundaries_allowed(self):
        a = parse_boundary(square_geojson(BASE_LON, BASE_LAT))
        b = parse_boundary(square_geojson(BASE_LON + 0.001, BASE_LAT))  # shares an edge
        assert find_overlap(b, [("UIN-A", a)]) is None
        assert intersection_area_sqm(a, b) == 0.0

    def test_real_overlap_rejected(self):
        a = parse_boundary(square_geojson(BASE_LON, BASE_LAT))
        b = parse_boundary(square_geojson(BASE_LON + 0.0005, BASE_LAT))  # 50% overlap
        err = find_overlap(b, [("UIN-A", a)])
        assert err is not None
        assert err.conflicting_parcel_uin == "UIN-A"
        assert err.overlap_area_m2 > 1_000  # roughly half of ~12,000 m²

    def test_disjoint_parcels_allowed(self):
        a = parse_boundary(square_geojson(BASE_LON, BASE_LAT))
        b = parse_boundary(square_geojson(BASE_LON + 0.01, BASE_LAT))
        assert find_overlap(b, [("UIN-A", a)]) is None
