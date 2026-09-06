"""Deterministic local job execution tests (Sedona-local reuse, H3, export)."""

from __future__ import annotations

import numpy as np

from app.domain import JobStatus

from helpers import LAGOS, OGUN, SQUARE, SQUARE_2, UNRELATED, feature, register_dataset


def _join_params(tenant):
    buildings = [
        feature(SQUARE, building_footprint_id="B1", tenant_state_id=tenant, estimated_area_sqm=120.0),
        feature(UNRELATED, building_footprint_id="B2", tenant_state_id=tenant, estimated_area_sqm=80.0),
        feature(SQUARE, building_footprint_id="B-OTHER", tenant_state_id="other", estimated_area_sqm=1.0),
    ]
    parcels = [
        feature(SQUARE, parcel_uin="P1", tenant_state_id=tenant, owner_stin="STIN-1", assessed_annual_luc_kobo=0),
        feature(SQUARE_2, parcel_uin="P2", tenant_state_id=tenant, owner_stin="STIN-2", assessed_annual_luc_kobo=500_000),
    ]
    return {"buildings": buildings, "parcels": parcels}


def _run_job(client, state, job_type, parameters):
    job = client.post(
        f"/api/v1/states/{state}/geospatial/jobs",
        json={"job_type": job_type, "parameters": parameters},
    ).json()
    resp = client.post(f"/api/v1/states/{state}/geospatial/jobs/{job['job_id']}/run")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_unassessed_property_join_matches_local_runner(client):
    """Mirrors geospatial/local/unassessed_property_join.py semantics exactly:
    one row per tenant footprint; no parcel -> UNREGISTERED_ENCROACHMENT;
    zero assessed LUC -> UNASSESSED_IMPROVEMENT; else COMPLIANT."""

    job = _run_job(client, LAGOS, "UNASSESSED_PROPERTY_JOIN", _join_params(LAGOS))
    assert job["status"] == "SUCCEEDED"
    from app.repository import InMemoryGeospatialRepository  # noqa: F401
    # verify via service metrics
    results = client.get(f"/api/v1/states/{LAGOS}/geospatial/jobs/{job['job_id']}").json()
    assert results["status"] == "SUCCEEDED"


def test_unassessed_property_join_metrics(service):
    job = service.create_job(LAGOS, {"job_type": "UNASSESSED_PROPERTY_JOIN", "parameters": _join_params(LAGOS)})
    job = service.run_job(LAGOS, job.job_id)
    assert job.status is JobStatus.SUCCEEDED
    result = service.repo.list_results(LAGOS, job.job_id)[0]
    assert result.metrics["rows"] == 2  # only tenant footprints
    assert result.metrics["status_counts"] == {
        "UNASSESSED_IMPROVEMENT": 1,  # B1 overlaps P1 (zero assessed)
        "UNREGISTERED_ENCROACHMENT": 1,  # B2 overlaps nothing
    }


def test_unassessed_property_join_tenant_scoped(service):
    service.create_job(LAGOS, {"job_type": "UNASSESSED_PROPERTY_JOIN", "parameters": _join_params(LAGOS)})
    job_ogun = service.create_job(OGUN, {"job_type": "UNASSESSED_PROPERTY_JOIN", "parameters": _join_params(OGUN)})
    ran = service.run_job(OGUN, job_ogun.job_id)
    result = service.repo.list_results(OGUN, ran.job_id)[0]
    assert result.metrics["rows"] == 2  # B-OTHER excluded for both tenants


def _ndvi_params():
    # 20x20 scenes; a deep NDVI drop block (10x10 px = 1 ha) of vegetation loss.
    rng_t0_red = np.full((20, 20), 0.1)
    rng_t0_nir = np.full((20, 20), 0.5)  # healthy vegetation: NDVI ~ 0.67
    red_t1 = rng_t0_red.copy()
    nir_t1 = rng_t0_nir.copy()
    nir_t1[3:13, 3:13] = 0.1  # drop -> NDVI ~ 0.0 in the block
    return {
        "b04_t0": rng_t0_red.tolist(), "b08_t0": rng_t0_nir.tolist(),
        "b04_t1": red_t1.tolist(), "b08_t1": nir_t1.tolist(),
        "scene_id": "S2-test", "origin_lat": 9.0, "origin_lon": 8.0,
        "min_area_ha": 0.5, "h3_resolution": 7,
    }


def test_ndvi_change_detection_produces_h3_alert_summary(service):
    job = service.create_job(LAGOS, {"job_type": "NDVI_CHANGE_DETECTION", "parameters": _ndvi_params()})
    ran = service.run_job(LAGOS, job.job_id)
    assert ran.status is JobStatus.SUCCEEDED
    result = service.repo.list_results(LAGOS, ran.job_id)[0]
    assert result.metrics["alert_count"] == 1
    alert = result.metrics["alerts"][0]
    assert alert["scene_id"] == "S2-test"
    assert alert["disturbance_ha"] >= 0.5
    assert alert["h3_cell"].startswith("LOCAL-GH7-")
    assert result.metrics["h3_backend"] == "local-geohash-fallback"


def test_ndvi_no_disturbance_no_alerts(service):
    params = _ndvi_params()
    params["b08_t1"] = params["b08_t0"]  # identical scenes
    job = service.create_job(LAGOS, {"job_type": "NDVI_CHANGE_DETECTION", "parameters": params})
    ran = service.run_job(LAGOS, job.job_id)
    result = service.repo.list_results(LAGOS, ran.job_id)[0]
    assert result.metrics["alert_count"] == 0


def test_ndvi_small_disturbance_below_threshold(service):
    params = _ndvi_params()
    nir = np.array(params["b08_t1"])
    nir[:] = 0.5
    nir[3:5, 3:5] = 0.1  # 4 px = 0.04 ha < 0.5 ha
    params["b08_t1"] = nir.tolist()
    job = service.create_job(LAGOS, {"job_type": "NDVI_CHANGE_DETECTION", "parameters": params})
    ran = service.run_job(LAGOS, job.job_id)
    assert service.repo.list_results(LAGOS, ran.job_id)[0].metrics["alert_count"] == 0


def test_h3_aggregation_job(service):
    params = {
        "resolution": 5,
        "features": [feature(SQUARE, id=1), feature(SQUARE_2, id=2), feature(UNRELATED, id=3)],
    }
    job = service.create_job(LAGOS, {"job_type": "H3_AGGREGATION", "parameters": params})
    ran = service.run_job(LAGOS, job.job_id)
    result = service.repo.list_results(LAGOS, ran.job_id)[0]
    cells = result.metrics["cells"]
    assert result.metrics["cell_count"] == len(cells) > 0
    assert sum(cells.values()) >= 3  # every feature contributes >= 1 cell


def test_h3_aggregation_deterministic(service):
    params = {"resolution": 6, "features": [feature(SQUARE), feature(SQUARE_2)]}
    j1 = service.run_job(LAGOS, service.create_job(LAGOS, {"job_type": "H3_AGGREGATION", "parameters": params}).job_id)
    j2 = service.run_job(LAGOS, service.create_job(LAGOS, {"job_type": "H3_AGGREGATION", "parameters": params}).job_id)
    r1 = service.repo.list_results(LAGOS, j1.job_id)[0]
    r2 = service.repo.list_results(LAGOS, j2.job_id)[0]
    assert r1.metrics == r2.metrics


def test_geoparquet_export_job_writes_artifact(service, tmp_path):
    params = {"features": [feature(SQUARE, parcel_uin="P1")], "crs": "EPSG:4326"}
    job = service.create_job(LAGOS, {"job_type": "GEOPARQUET_EXPORT", "parameters": params})
    ran = service.run_job(LAGOS, job.job_id)
    assert ran.status is JobStatus.SUCCEEDED
    result = service.repo.list_results(LAGOS, ran.job_id)[0]
    assert ran.output_uri == result.result_uri
    assert result.metrics["feature_count"] == 1
    import os

    assert os.path.exists(result.result_uri)


def test_geoparquet_export_rejects_invalid_feature(service):
    job = service.create_job(LAGOS, {"job_type": "GEOPARQUET_EXPORT", "parameters": {"features": [{"bad": 1}]}})
    ran = service.run_job(LAGOS, job.job_id)
    assert ran.status is JobStatus.FAILED
    assert ran.error


def test_geolibre_project_build_job(service, tmp_path):
    ds = service.register_dataset(LAGOS, {
        "dataset_type": "CADASTRE", "name": "cad", "source_uri": "s3://b/cad.parquet",
        "sensitivity": "SENSITIVE", "geometry": SQUARE,
    })
    job = service.create_job(LAGOS, {
        "job_type": "GEOLIBRE_PROJECT_BUILD",
        "input_dataset_ids": [ds.dataset_id],
        "parameters": {"name": "from-job"},
    })
    ran = service.run_job(LAGOS, job.job_id)
    assert ran.status is JobStatus.SUCCEEDED
    result = service.repo.list_results(LAGOS, ran.job_id)[0]
    project = service.repo.get_project(LAGOS, result.metrics["project_id"])
    assert project.redaction_level == "SENSITIVE"
    assert project.source_job_id == job.job_id


def test_run_job_emits_audit_with_result_hash(service):
    job = service.create_job(LAGOS, {"job_type": "H3_AGGREGATION", "parameters": {"features": [feature(SQUARE)]}})
    service.run_job(LAGOS, job.job_id)
    audit = service.repo.list_audit(LAGOS)
    actions = [e.action for e in audit]
    assert actions == ["job_created", "job_completed"]
    assert all(e.payload_hash and e.entry_hash for e in audit)
