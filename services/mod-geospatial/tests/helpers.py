"""Shared fixtures/helpers for mod-geospatial tests."""

from __future__ import annotations

LAGOS = "lagos"
OGUN = "ogun"

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[8.0, 9.0], [8.001, 9.0], [8.001, 9.001], [8.0, 9.001], [8.0, 9.0]]],
}
SQUARE_2 = {
    "type": "Polygon",
    "coordinates": [[[8.002, 9.002], [8.003, 9.002], [8.003, 9.003], [8.002, 9.003], [8.002, 9.002]]],
}
UNRELATED = {
    "type": "Polygon",
    "coordinates": [[[7.0, 8.0], [7.001, 8.0], [7.001, 8.001], [7.0, 8.001], [7.0, 8.0]]],
}


def feature(geometry, **props):
    return {"type": "Feature", "geometry": geometry, "properties": props}


def register_dataset(client, state, name="cadastre", **overrides):
    body = {
        "dataset_type": "CADASTRE",
        "name": name,
        "source_uri": "s3://lakehouse-bronze/lagos/cadastre.parquet",
        "sensitivity": "INTERNAL",
        "geometry": SQUARE,
    }
    body.update(overrides)
    resp = client.post(f"/api/v1/states/{state}/geospatial/datasets", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()
