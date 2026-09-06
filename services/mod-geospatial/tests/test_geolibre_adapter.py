"""GeoLibre project generation and redaction tests."""

from __future__ import annotations

import json

import pytest

from app.adapters.geolibre_adapter import (
    LocalGeoLibreProjectBuilder,
    RedactionError,
    build_layer,
    get_geolibre_builder,
    validate_source_url,
)
from app.adapters.geolibre_wasm_adapter import GeoLibreWasmAdapter
from app.domain import AdapterUnavailableError

from helpers import LAGOS, SQUARE, register_dataset


def test_builder_factory_defaults_to_labeled_local_fallback():
    builder = get_geolibre_builder()
    assert isinstance(builder, LocalGeoLibreProjectBuilder)
    assert builder.backend_name == "local-fallback"


def test_build_layer_rejects_hosted_geolibre_url():
    with pytest.raises(RedactionError):
        build_layer(layer_id="l", title="t", source_url="https://web.geolibre.app/shared/abc")


def test_build_layer_rejects_geolibre_app_subdomain():
    with pytest.raises(RedactionError):
        validate_source_url("https://data.geolibre.app/x")


def test_build_layer_rejects_embedded_credentials():
    with pytest.raises(RedactionError):
        validate_source_url("https://user:pass@selfhosted.example.com/layer")


def test_build_layer_rejects_disallowed_scheme():
    with pytest.raises(RedactionError):
        validate_source_url("ftp://example.com/x")


def test_build_layer_accepts_selfhosted_and_object_storage():
    for url in ("https://gis.lagos.example.com/tiles/{z}/{x}/{y}", "s3://bucket/key.parquet", "file:///srv/data/x.json"):
        layer = build_layer(layer_id="l", title="t", source_url=url)
        assert layer["source"]["url"] == url


def test_sensitive_layer_is_metadata_only():
    layer = build_layer(
        layer_id="l", title="t", source_url="s3://b/x", sensitive=True, geometry_hash="abc123"
    )
    assert layer["redacted"] is True
    assert layer["source"]["data"] == "REDACTED"
    assert layer["source"]["geometry_sha256"] == "abc123"
    assert "coordinates" not in json.dumps(layer)


def test_project_build_endpoint(client, tmp_path):
    ds = register_dataset(client, LAGOS, sensitivity="SENSITIVE")
    resp = client.post(
        f"/api/v1/states/{LAGOS}/geospatial/geolibre/projects",
        json={"name": "lagos-workbench", "dataset_ids": [ds["dataset_id"]]},
    )
    assert resp.status_code == 201, resp.text
    project = resp.json()
    assert project["redaction_level"] == "SENSITIVE"
    assert project["project_hash"]
    with open(project["project_uri"], encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["schema"] == "geolibre.project/v1"
    assert doc["layers"][0]["redacted"] is True
    assert "web.geolibre.app" not in json.dumps(doc)


def test_project_build_with_foreign_dataset_404(client):
    ds = register_dataset(client, LAGOS)
    resp = client.post(
        "/api/v1/states/ogun/geolibre/projects",
        json={"name": "x", "dataset_ids": [ds["dataset_id"]]},
    )
    assert resp.status_code == 404


def test_project_build_hosted_url_rejected_422(client):
    ds = register_dataset(client, LAGOS, source_uri="https://web.geolibre.app/shared/abc")
    resp = client.post(
        f"/api/v1/states/{LAGOS}/geospatial/geolibre/projects",
        json={"name": "x", "dataset_ids": [ds["dataset_id"]]},
    )
    assert resp.status_code == 422


def test_get_project_and_tenant_isolation(client):
    ds = register_dataset(client, LAGOS)
    project = client.post(
        f"/api/v1/states/{LAGOS}/geospatial/geolibre/projects",
        json={"name": "p", "dataset_ids": [ds["dataset_id"]]},
    ).json()
    got = client.get(f"/api/v1/states/{LAGOS}/geospatial/geolibre/projects/{project['project_id']}")
    assert got.status_code == 200
    assert client.get(f"/api/v1/states/ogun/geolibre/projects/{project['project_id']}").status_code == 404


def test_wasm_adapter_local_fallback_labeled():
    adapter = GeoLibreWasmAdapter()
    assert adapter.backend_name == "local-python-fallback"
    result = adapter.run_tool("centroid", {"geometry": SQUARE})
    assert result["backend"] == "local-python-fallback"
    assert result["geometry"]["type"] == "Point"


def test_wasm_adapter_area_tool():
    adapter = GeoLibreWasmAdapter()
    result = adapter.run_tool("area", {"geometry": SQUARE})
    assert result["area"] > 0


def test_wasm_adapter_buffer_tool():
    adapter = GeoLibreWasmAdapter()
    result = adapter.run_tool("buffer", {"geometry": {"type": "Point", "coordinates": [8.0, 9.0]}, "distance": 0.01})
    assert result["geometry"]["type"] == "Polygon"


def test_wasm_adapter_unknown_tool_fails_closed():
    adapter = GeoLibreWasmAdapter()
    with pytest.raises(AdapterUnavailableError):
        adapter.run_tool("whitebox-lidar-filters", {"geometry": SQUARE})


def test_wasm_adapter_fails_closed_in_production(monkeypatch):
    monkeypatch.setenv("GEOSPATIAL_MODE", "production")
    monkeypatch.delenv("GEOLIBRE_WASM", raising=False)
    with pytest.raises(AdapterUnavailableError):
        GeoLibreWasmAdapter()
