"""H3 adapter, lakehouse adapter, and production seam fail-closed tests."""

from __future__ import annotations

import json
import os

import pytest

from app.adapters.h3_adapter import LocalH3Adapter, get_h3_adapter
from app.adapters.lakehouse_adapter import LakehouseAdapter, _parquet_stack_available
from app.adapters.postgis_adapter import PostGISConnectionFactory
from app.adapters.sedona_adapter import SedonaAdapter
from app.domain import AdapterUnavailableError
from app.repository import PostGISGeospatialRepository

from helpers import SQUARE, SQUARE_2, feature


def test_local_h3_backend_is_clearly_labeled():
    adapter = LocalH3Adapter()
    assert adapter.backend_name == "local-geohash-fallback"
    cell = adapter.cell_for_point(9.0, 8.0, 9)
    assert cell.startswith("LOCAL-GH9-")


def test_local_h3_is_deterministic():
    a, b = LocalH3Adapter(), LocalH3Adapter()
    assert a.cell_for_point(9.05, 8.07, 7) == b.cell_for_point(9.05, 8.07, 7)


def test_local_h3_resolution_changes_precision():
    adapter = LocalH3Adapter()
    coarse = adapter.cell_for_point(9.0, 8.0, 1)
    fine = adapter.cell_for_point(9.0, 8.0, 15)
    assert len(fine) > len(coarse)


def test_local_h3_nearby_points_share_coarse_cell():
    adapter = LocalH3Adapter()
    c1 = adapter.cell_for_point(9.0001, 8.0001, 1)
    c2 = adapter.cell_for_point(9.0002, 8.0002, 1)
    assert c1 == c2


def test_cells_for_geometry_sorted_unique():
    adapter = LocalH3Adapter()
    cells = adapter.cells_for_geometry(SQUARE, 5)
    assert cells == sorted(set(cells)) and len(cells) >= 1


def test_h3_factory_falls_back_outside_production(monkeypatch):
    monkeypatch.setenv("GEOSPATIAL_MODE", "local")
    adapter = get_h3_adapter(prefer_real=True)
    assert isinstance(adapter, LocalH3Adapter)


def test_h3_factory_fails_closed_in_production(monkeypatch):
    pytest.importorskip("importlib")
    import importlib

    if importlib.util.find_spec("h3") is not None:
        pytest.skip("h3 installed in this environment")
    monkeypatch.setenv("GEOSPATIAL_MODE", "production")
    with pytest.raises(AdapterUnavailableError):
        get_h3_adapter(prefer_real=True)


def test_lakehouse_json_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOSPATIAL_MODE", "local")
    adapter = LakehouseAdapter(str(tmp_path))
    features = [feature(SQUARE, parcel_uin="P1"), feature(SQUARE_2, parcel_uin="P2")]
    manifest = adapter.export_features(features, "export-test")
    if _parquet_stack_available():
        assert manifest["format"] == "geoparquet"
        assert manifest["uri"].endswith(".parquet")
    else:
        assert manifest["format"] == "geojson-local-fallback"
        assert manifest["local_fallback"] is True
        assert manifest["uri"].endswith(".json")
        with open(manifest["uri"], encoding="utf-8") as fh:
            doc = json.load(fh)
        assert "LOCAL FALLBACK" in doc["note"]
        assert len(doc["features"]) == 2
    assert manifest["feature_count"] == 2
    assert manifest["crs"] == "EPSG:4326"
    assert len(manifest["sha256"]) == 64


def test_lakehouse_fallback_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOSPATIAL_MODE", "local")
    adapter = LakehouseAdapter(str(tmp_path))
    features = [feature(SQUARE, a=1, b=2)]
    m1 = adapter.export_features(features, "e1")
    m2 = adapter.export_features(features, "e2")
    assert m1["sha256"] == m2["sha256"]


def test_lakehouse_fails_closed_in_production_without_parquet(tmp_path, monkeypatch):
    if _parquet_stack_available():
        pytest.skip("parquet stack installed")
    monkeypatch.setenv("GEOSPATIAL_MODE", "production")
    adapter = LakehouseAdapter(str(tmp_path))
    with pytest.raises(AdapterUnavailableError):
        adapter.export_features([feature(SQUARE)], "e")


def test_postgis_repository_fails_closed_without_driver_or_dsn(monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_POSTGIS_DSN", raising=False)
    with pytest.raises(AdapterUnavailableError):
        PostGISGeospatialRepository(dsn=None)


def test_postgis_connection_factory_fails_closed_without_dsn(monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_POSTGIS_DSN", raising=False)
    with pytest.raises(AdapterUnavailableError):
        PostGISConnectionFactory()


def test_sedona_adapter_fails_closed_without_endpoint(monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_SEDONA_ENDPOINT", raising=False)
    with pytest.raises(AdapterUnavailableError):
        SedonaAdapter()


def test_sedona_adapter_submit_never_noops(monkeypatch):
    adapter = SedonaAdapter(endpoint="spark://cluster:7077")
    with pytest.raises(AdapterUnavailableError):
        adapter.submit_job("UNASSESSED_PROPERTY_JOIN", {})
