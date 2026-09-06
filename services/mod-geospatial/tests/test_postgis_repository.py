"""Tests for the PostGIS geospatial repository.

Two tiers:

* **Deterministic fake** (always runs): a fake psycopg module + fake
  connection/cursor injected via ``connect=``; asserts SQL mapping, RLS
  pinning, row mapping, and fail-closed construction.
* **Real PostGIS** (skip-gated): when ``GEOSPATIAL_TEST_POSTGIS_DSN`` is set,
  a round-trip test against the migrated schema runs instead.
"""

from __future__ import annotations

import os
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest

from app.domain import (
    AdapterUnavailableError,
    AuditEntry,
    Dataset,
    DatasetType,
    GeolibreProject,
    JobResult,
    JobStatus,
    JobType,
    NotFoundError,
    ProcessingJob,
    Sensitivity,
)
from app.repository import PostGISGeospatialRepository


# ---------------------------------------------------------------------------
# Fake psycopg stack
# ---------------------------------------------------------------------------


class FakeJsonb:
    def __init__(self, value):
        self.value = value


def _install_fake_psycopg(monkeypatch):
    psycopg = types.ModuleType("psycopg")
    types_mod = types.ModuleType("psycopg.types")
    json_mod = types.ModuleType("psycopg.types.json")
    json_mod.Jsonb = FakeJsonb
    types_mod.json = json_mod
    psycopg.types = types_mod
    psycopg.connect = lambda dsn: None  # replaced via connect= injection
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.types", types_mod)
    monkeypatch.setitem(sys.modules, "psycopg.types.json", json_mod)
    return psycopg


class FakeCursor:
    """Records statements; serves queued result rows for SELECTs."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.statements.append((" ".join(sql.split()), params))
        self._pending = self.conn.pop_results(sql)

    def fetchone(self):
        return self._pending[0] if self._pending else None

    def fetchall(self):
        return list(self._pending)


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.results = {}  # substring -> list of rows for next matching query
        self.closed = False
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def pop_results(self, sql):
        for key, rows in list(self.results.items()):
            if key in sql:
                del self.results[key]
                return rows
        return []

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_psycopg(monkeypatch):
    return _install_fake_psycopg(monkeypatch)


@pytest.fixture()
def fake_conn():
    return FakeConnection()


@pytest.fixture()
def repo(fake_psycopg, fake_conn):
    return PostGISGeospatialRepository("postgresql://fake/fake", connect=lambda dsn: fake_conn)


DATASET_ROW = (
    uuid.uuid4(),
    "osun",
    "CADASTRE",
    "osun parcels",
    "s3://lake/parcels.geojson",
    "EPSG:4326",
    {"geometry_sha256": "ab" * 32},
    "INTERNAL",
    9,
    datetime(2025, 1, 1, tzinfo=timezone.utc),
)

JOB_ROW = (
    uuid.uuid4(),
    "osun",
    "H3_AGGREGATION",
    "QUEUED",
    [uuid.uuid4()],
    {"resolution": 9},
    None,
    None,
    datetime(2025, 1, 1, tzinfo=timezone.utc),
    None,
    None,
)


def test_fail_closed_without_dsn(fake_psycopg, monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_POSTGIS_DSN", raising=False)
    with pytest.raises(AdapterUnavailableError):
        PostGISGeospatialRepository()


def test_fail_closed_without_driver(monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_POSTGIS_DSN", raising=False)
    for mod in ("psycopg", "psycopg.types", "psycopg.types.json"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    with pytest.raises(AdapterUnavailableError):
        PostGISGeospatialRepository("postgresql://fake/fake", connect=lambda dsn: None)


def test_connection_pinned_to_tenant_via_rls(repo, fake_conn):
    repo.list_datasets("osun")
    first = fake_conn.statements[0]
    assert "set_config('app.current_state_tenant'" in first[0]
    assert first[1] == ("osun",)


def test_connections_cached_per_tenant(fake_psycopg):
    conns = {}

    def connect(dsn):
        conn = FakeConnection()
        conns[id(conn)] = conn
        return conn

    repo = PostGISGeospatialRepository("postgresql://fake/fake", connect=connect)
    repo.list_datasets("osun")
    repo.list_datasets("osun")
    repo.list_datasets("lagos")
    assert len(conns) == 2  # one pinned connection per tenant


def test_dataset_roundtrip_mapping(repo, fake_conn):
    fake_conn.results["FROM geospatial.datasets WHERE dataset_id"] = [DATASET_ROW]
    ds = repo.get_dataset("osun", str(DATASET_ROW[0]))
    assert ds.dataset_id == DATASET_ROW[0].hex
    assert ds.tenant_state_id == "osun"
    assert ds.dataset_type is DatasetType.CADASTRE
    assert ds.sensitivity is Sensitivity.INTERNAL
    assert ds.h3_resolution == 9
    assert ds.geometry_metadata == {"geometry_sha256": "ab" * 32}


def test_get_dataset_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.get_dataset("osun", uuid.uuid4().hex)


def test_save_dataset_inserts_tenant_scoped_row(repo, fake_conn):
    ds = Dataset(
        dataset_id=uuid.uuid4().hex,
        tenant_state_id="osun",
        dataset_type=DatasetType.CADASTRE,
        name="osun parcels",
        source_uri="s3://lake/parcels.geojson",
    )
    repo.save_dataset(ds)
    sql, params = fake_conn.statements[-1]
    assert "INSERT INTO geospatial.datasets" in sql
    assert params[1] == "osun"
    assert isinstance(params[6], FakeJsonb)  # geometry_metadata as JSONB


def test_job_roundtrip_mapping(repo, fake_conn):
    fake_conn.results["FROM geospatial.processing_jobs WHERE job_id"] = [JOB_ROW]
    job = repo.get_job("osun", str(JOB_ROW[0]))
    assert job.job_id == JOB_ROW[0].hex
    assert job.job_type is JobType.H3_AGGREGATION
    assert job.status is JobStatus.QUEUED
    assert job.input_dataset_ids == [JOB_ROW[4][0].hex]
    assert job.parameters == {"resolution": 9}


def test_save_result_and_list(repo, fake_conn):
    result_id, job_id = uuid.uuid4(), uuid.uuid4()
    fake_conn.results["FROM geospatial.processing_jobs WHERE job_id"] = [JOB_ROW]
    row = (
        result_id,
        job_id,
        "osun",
        "H3_CELLS",
        None,
        {"cell_count": 3},
        "cd" * 32,
        datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    fake_conn.results["FROM geospatial.job_results WHERE job_id"] = [row]
    results = repo.list_results("osun", str(job_id))
    assert len(results) == 1
    assert results[0].result_id == result_id.hex
    assert results[0].result_hash == "cd" * 32


def test_project_roundtrip(repo, fake_conn):
    project_id, job_id = uuid.uuid4(), uuid.uuid4()
    row = (
        project_id,
        "osun",
        "field workbench",
        "/var/project.geolibre.json",
        "ef" * 32,
        "SENSITIVE",
        job_id,
        datetime(2025, 1, 3, tzinfo=timezone.utc),
    )
    fake_conn.results["FROM geospatial.geolibre_projects WHERE project_id"] = [row]
    proj = repo.get_project("osun", str(project_id))
    assert proj.project_id == project_id.hex
    assert proj.redaction_level == "SENSITIVE"
    assert proj.source_job_id == job_id.hex


def test_audit_chain(repo, fake_conn):
    entry = AuditEntry(
        sequence=1,
        tenant_state_id="osun",
        action="dataset_registered",
        resource_type="dataset",
        resource_id=uuid.uuid4().hex,
        payload_hash="12" * 32,
        object_uri=None,
        prev_hash="GENESIS",
        entry_hash="34" * 32,
        created_at="2025-01-01T00:00:00Z",
    )
    repo.append_audit(entry)
    sql, params = fake_conn.statements[-1]
    assert "INSERT INTO geospatial.audit_log" in sql
    assert params[7] == "GENESIS"

    assert repo.last_audit_hash("osun") == "GENESIS"  # no rows
    fake_conn.results["SELECT entry_hash FROM geospatial.audit_log"] = [("34" * 32,)]
    assert repo.last_audit_hash("osun") == "34" * 32


def test_close_closes_all_connections(repo, fake_conn):
    repo.list_datasets("osun")
    repo.close()
    assert fake_conn.closed


# ---------------------------------------------------------------------------
# Real PostGIS tier (skip-gated)
# ---------------------------------------------------------------------------

TEST_DSN = os.environ.get("GEOSPATIAL_TEST_POSTGIS_DSN")


@pytest.mark.skipif(not TEST_DSN, reason="GEOSPATIAL_TEST_POSTGIS_DSN not set; no PostGIS available")
def test_real_postgis_roundtrip():
    pytest.importorskip("psycopg")
    repo = PostGISGeospatialRepository(TEST_DSN)
    ds = Dataset(
        dataset_id=uuid.uuid4().hex,
        tenant_state_id="osun",
        dataset_type=DatasetType.CADASTRE,
        name="itest parcels",
        source_uri="s3://lake/itest.geojson",
    )
    repo.save_dataset(ds)
    loaded = repo.get_dataset("osun", ds.dataset_id)
    assert loaded.name == "itest parcels"
    # RLS: the row is invisible to another tenant.
    with pytest.raises(NotFoundError):
        repo.get_dataset("lagos", ds.dataset_id)
    repo.close()
