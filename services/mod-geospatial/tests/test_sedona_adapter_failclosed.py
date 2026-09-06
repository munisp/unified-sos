"""Fail-closed contract tests for the Sedona cluster submission adapter."""

from __future__ import annotations

import pytest

from app.adapters.sedona_adapter import SedonaAdapter
from app.domain import AdapterUnavailableError


def test_fail_closed_without_endpoint(monkeypatch):
    monkeypatch.delenv("GEOSPATIAL_SEDONA_ENDPOINT", raising=False)
    with pytest.raises(AdapterUnavailableError, match="GEOSPATIAL_SEDONA_ENDPOINT"):
        SedonaAdapter()


def test_fail_closed_without_spark_stack(monkeypatch):
    # Endpoint set but no pyspark/sedona importable and no injected session.
    if SedonaAdapter._spark_stack_available():
        pytest.skip("spark stack installed in this environment")
    with pytest.raises(AdapterUnavailableError, match="fails closed"):
        SedonaAdapter("spark://sedona:7077")


def test_fail_closed_unknown_job_type():
    adapter = SedonaAdapter("spark://sedona:7077", session=object())
    with pytest.raises(AdapterUnavailableError, match="no Sedona job spec"):
        adapter.submit_job("NOT_A_JOB", {})


def test_fail_closed_missing_spec_file(tmp_path):
    adapter = SedonaAdapter("spark://sedona:7077", spec_dir=str(tmp_path), session=object())
    with pytest.raises(AdapterUnavailableError, match="job spec missing"):
        adapter.submit_job("UNASSESSED_PROPERTY_JOIN", {})


class _FakeConf:
    def __init__(self):
        self.settings = {}

    def set(self, key, value):
        self.settings[key] = value


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class _FakeSparkContext:
    def __init__(self):
        self.py_files = []

    def addPyFile(self, path):
        self.py_files.append(path)


class _FakeSession:
    def __init__(self):
        self.conf = _FakeConf()
        self.sparkContext = _FakeSparkContext()
        self.sql_calls = []

    def sql(self, statement):
        self.sql_calls.append(statement)
        return _FakeResult([("b1", "p1", "COMPLIANT"), ("b2", None, "UNREGISTERED_ENCROACHMENT")])


def test_submit_sql_job_with_injected_session(tmp_path):
    spec = tmp_path / "unassessed_property_join.sql"
    spec.write_text("SELECT 1", encoding="utf-8")
    session = _FakeSession()
    adapter = SedonaAdapter("spark://sedona:7077", spec_dir=str(tmp_path), session=session)
    out = adapter.submit_job("UNASSESSED_PROPERTY_JOIN", {"tenant_state_id": "ogun"})
    assert out["engine"] == "sedona-sql"
    assert out["row_count"] == 2
    assert session.sql_calls == ["SELECT 1"]
    assert session.conf.settings["sos.job.param.tenant_state_id"] == "ogun"


def test_submit_py_job_with_injected_session(tmp_path):
    spec = tmp_path / "ndvi_change_detection.py"
    spec.write_text("# job\n", encoding="utf-8")
    session = _FakeSession()
    adapter = SedonaAdapter("spark://sedona:7077", spec_dir=str(tmp_path), session=session)
    out = adapter.submit_job("NDVI_CHANGE_DETECTION", {"scene_id": "s2-t1"})
    assert out["engine"] == "sedona-pyspark"
    assert out["submitted"] is True
    assert session.sparkContext.py_files == [str(spec)]


def test_default_spec_dir_has_versioned_specs():
    adapter = SedonaAdapter("spark://sedona:7077", session=object())
    for job_type in ("UNASSESSED_PROPERTY_JOIN", "NDVI_CHANGE_DETECTION"):
        assert adapter.job_spec_path(job_type).is_file()
