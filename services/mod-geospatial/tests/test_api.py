"""API surface tests: endpoints, tenant isolation, job state machine, audit."""

from __future__ import annotations

import pytest

from app.domain import JobStatus, JobTransitionError, ProcessingJob, JobType

from helpers import LAGOS, OGUN, SQUARE, register_dataset

BASE = "/api/v1/states/{s}/geospatial"


def url(state, path=""):
    return BASE.format(s=state) + path


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["h3_backend"] == "local-geohash-fallback"


def test_register_dataset_created(client):
    ds = register_dataset(client, LAGOS)
    assert ds["tenant_state_id"] == LAGOS
    assert ds["dataset_type"] == "CADASTRE"
    assert ds["geometry_metadata"]["geometry_sha256"]


def test_register_dataset_all_types(client):
    for dtype in (
        "CADASTRE", "BUILDING_FOOTPRINTS", "NDVI_ALERTS", "FOREST_RESERVES",
        "TRANSPORT_CORRIDORS", "MARKET_BOUNDARIES", "MINING_SITES",
    ):
        resp = client.post(url(LAGOS, "/datasets"), json={
            "dataset_type": dtype, "name": f"n-{dtype}", "source_uri": "s3://b/x",
        })
        assert resp.status_code == 201, resp.text


def test_register_dataset_invalid_type_rejected(client):
    resp = client.post(url(LAGOS, "/datasets"), json={
        "dataset_type": "NOPE", "name": "x", "source_uri": "s3://b/x",
    })
    assert resp.status_code == 422


def test_register_dataset_invalid_geometry_rejected(client):
    bad = {"type": "Polygon", "coordinates": [[[8.0, 9.0], [8.1, 9.0], [8.1, 9.1], [8.0, 9.1]]]}  # open ring
    resp = client.post(url(LAGOS, "/datasets"), json={
        "dataset_type": "CADASTRE", "name": "x", "source_uri": "s3://b/x", "geometry": bad,
    })
    assert resp.status_code == 422


def test_register_dataset_out_of_range_coordinate_rejected(client):
    bad = {"type": "Point", "coordinates": [200.0, 9.0]}
    resp = client.post(url(LAGOS, "/datasets"), json={
        "dataset_type": "CADASTRE", "name": "x", "source_uri": "s3://b/x", "geometry": bad,
    })
    assert resp.status_code == 422


def test_list_datasets_tenant_scoped(client):
    register_dataset(client, LAGOS, name="a")
    register_dataset(client, OGUN, name="b")
    lagos = client.get(url(LAGOS, "/datasets")).json()
    ogun = client.get(url(OGUN, "/datasets")).json()
    assert [d["name"] for d in lagos] == ["a"]
    assert [d["name"] for d in ogun] == ["b"]


def test_get_dataset_cross_tenant_is_404(client):
    ds = register_dataset(client, LAGOS)
    resp = client.get(url(OGUN, f"/datasets/{ds['dataset_id']}"))
    assert resp.status_code == 404


def test_get_dataset_not_found(client):
    assert client.get(url(LAGOS, "/datasets/missing")).status_code == 404


def test_create_job_and_get(client):
    resp = client.post(url(LAGOS, "/jobs"), json={"job_type": "H3_AGGREGATION", "parameters": {}})
    assert resp.status_code == 201
    job = resp.json()
    assert job["status"] == "QUEUED"
    got = client.get(url(LAGOS, f"/jobs/{job['job_id']}"))
    assert got.status_code == 200
    assert got.json()["job_id"] == job["job_id"]


def test_create_job_invalid_type_rejected(client):
    resp = client.post(url(LAGOS, "/jobs"), json={"job_type": "BOGUS"})
    assert resp.status_code == 422


def test_create_job_with_foreign_dataset_rejected(client):
    ds = register_dataset(client, LAGOS)
    resp = client.post(url(OGUN, "/jobs"), json={
        "job_type": "GEOPARQUET_EXPORT", "input_dataset_ids": [ds["dataset_id"]],
    })
    assert resp.status_code == 404


def test_list_jobs_tenant_scoped(client):
    client.post(url(LAGOS, "/jobs"), json={"job_type": "H3_AGGREGATION"})
    client.post(url(OGUN, "/jobs"), json={"job_type": "H3_AGGREGATION"})
    assert len(client.get(url(LAGOS, "/jobs")).json()) == 1
    assert len(client.get(url(OGUN, "/jobs")).json()) == 1


def test_get_job_cross_tenant_is_404(client):
    job = client.post(url(LAGOS, "/jobs"), json={"job_type": "H3_AGGREGATION"}).json()
    assert client.get(url(OGUN, f"/jobs/{job['job_id']}")).status_code == 404


def test_run_job_cross_tenant_is_404(client):
    job = client.post(url(LAGOS, "/jobs"), json={"job_type": "H3_AGGREGATION"}).json()
    assert client.post(url(OGUN, f"/jobs/{job['job_id']}/run")).status_code == 404


def test_rerun_succeeded_job_conflicts(client):
    job = client.post(url(LAGOS, "/jobs"), json={"job_type": "H3_AGGREGATION", "parameters": {"features": []}}).json()
    first = client.post(url(LAGOS, f"/jobs/{job['job_id']}/run"))
    assert first.status_code == 200 and first.json()["status"] == "SUCCEEDED"
    second = client.post(url(LAGOS, f"/jobs/{job['job_id']}/run"))
    assert second.status_code == 409


def test_job_state_machine_transitions():
    job = ProcessingJob(job_id="j", tenant_state_id="lagos", job_type=JobType.H3_AGGREGATION)
    with pytest.raises(JobTransitionError):
        job.transition(JobStatus.SUCCEEDED, "t")
    job.transition(JobStatus.RUNNING, "t1")
    assert job.started_at == "t1"
    job.transition(JobStatus.SUCCEEDED, "t2")
    assert job.completed_at == "t2"
    with pytest.raises(JobTransitionError):
        job.transition(JobStatus.RUNNING, "t3")


def test_failed_run_marks_job_failed(client, service):
    resp = client.post(url(LAGOS, "/jobs"), json={
        "job_type": "UNASSESSED_PROPERTY_JOIN", "parameters": {"buildings": [{"bad": True}]},
    })
    job = resp.json()
    ran = client.post(url(LAGOS, f"/jobs/{job['job_id']}/run"))
    assert ran.json()["status"] == "FAILED"
    assert ran.json()["error"]


def test_h3_index_endpoint(client):
    resp = client.post(url(LAGOS, "/h3/index"), json={"geometry": SQUARE, "resolution": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["h3_backend"] == "local-geohash-fallback"
    assert body["cells"] and all(c.startswith("LOCAL-GH7-") for c in body["cells"])


def test_h3_index_invalid_geometry(client):
    resp = client.post(url(LAGOS, "/h3/index"), json={"geometry": {"type": "Nope"}, "resolution": 5})
    assert resp.status_code == 422


def test_audit_endpoint_tenant_scoped_and_hash_chained(client):
    register_dataset(client, LAGOS, name="a1")
    register_dataset(client, LAGOS, name="a2")
    register_dataset(client, OGUN, name="b1")
    lagos_audit = client.get(url(LAGOS, "/audit")).json()
    ogun_audit = client.get(url(OGUN, "/audit")).json()
    assert len(lagos_audit) == 2 and len(ogun_audit) == 1
    assert lagos_audit[0]["prev_hash"] == "GENESIS"
    assert lagos_audit[1]["prev_hash"] == lagos_audit[0]["entry_hash"]
    assert all(e["tenant_state_id"] == LAGOS for e in lagos_audit)


def test_audit_is_hash_only_no_raw_geometry(client):
    register_dataset(client, LAGOS, name="s", sensitivity="SENSITIVE", geometry=SQUARE)
    audit = client.get(url(LAGOS, "/audit")).json()
    blob = str(audit)
    assert "8.001" not in blob and "coordinates" not in blob
    assert all(e["payload_hash"] and e["entry_hash"] for e in audit)
