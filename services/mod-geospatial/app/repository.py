"""Repository layer.

``InMemoryGeospatialRepository`` backs local/test mode.
``PostGISGeospatialRepository`` is the production system of record backed by
the schema in ``db/migrations/0006_geospatial.sql``. It fails closed unless
psycopg and a DSN are both present, pins every pooled connection to a single
tenant via ``app.current_state_tenant`` (Row-Level Security), and never falls
back to an unauthenticated store.
"""

from __future__ import annotations

import os
import threading
import uuid as _uuid
from typing import Any, Callable, Optional

from .domain import (
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
    TenantIsolationError,
)

POSTGIS_DSN_ENV = "GEOSPATIAL_POSTGIS_DSN"


class InMemoryGeospatialRepository:
    """Deterministic in-memory store. All reads/writes require the tenant id
    and cross-tenant access raises ``TenantIsolationError``/``NotFoundError``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._datasets: dict[str, Dataset] = {}
        self._jobs: dict[str, ProcessingJob] = {}
        self._results: dict[str, list[JobResult]] = {}
        self._projects: dict[str, GeolibreProject] = {}
        self._audit: list[AuditEntry] = []

    # -- datasets ---------------------------------------------------------
    def save_dataset(self, dataset: Dataset) -> Dataset:
        with self._lock:
            self._datasets[dataset.dataset_id] = dataset
        return dataset

    def get_dataset(self, tenant_state_id: str, dataset_id: str) -> Dataset:
        ds = self._datasets.get(dataset_id)
        if ds is None or ds.tenant_state_id != tenant_state_id:
            raise NotFoundError(dataset_id)
        return ds

    def list_datasets(self, tenant_state_id: str) -> list[Dataset]:
        return [d for d in self._datasets.values() if d.tenant_state_id == tenant_state_id]

    # -- jobs ---------------------------------------------------------------
    def save_job(self, job: ProcessingJob) -> ProcessingJob:
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get_job(self, tenant_state_id: str, job_id: str) -> ProcessingJob:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_state_id != tenant_state_id:
            raise NotFoundError(job_id)
        return job

    def list_jobs(self, tenant_state_id: str) -> list[ProcessingJob]:
        return [j for j in self._jobs.values() if j.tenant_state_id == tenant_state_id]

    # -- results ------------------------------------------------------------
    def save_result(self, result: JobResult) -> JobResult:
        with self._lock:
            self._results.setdefault(result.job_id, []).append(result)
        return result

    def list_results(self, tenant_state_id: str, job_id: str) -> list[JobResult]:
        job = self.get_job(tenant_state_id, job_id)  # enforces tenant scope
        return list(self._results.get(job.job_id, []))

    # -- geolibre projects --------------------------------------------------
    def save_project(self, project: GeolibreProject) -> GeolibreProject:
        with self._lock:
            self._projects[project.project_id] = project
        return project

    def get_project(self, tenant_state_id: str, project_id: str) -> GeolibreProject:
        proj = self._projects.get(project_id)
        if proj is None or proj.tenant_state_id != tenant_state_id:
            raise NotFoundError(project_id)
        return proj

    # -- audit --------------------------------------------------------------
    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        with self._lock:
            self._audit.append(entry)
        return entry

    def list_audit(self, tenant_state_id: str) -> list[AuditEntry]:
        return [e for e in self._audit if e.tenant_state_id == tenant_state_id]

    def last_audit_hash(self, tenant_state_id: str) -> str:
        entries = self.list_audit(tenant_state_id)
        return entries[-1].entry_hash if entries else "GENESIS"


def _uuid_str(value: Any) -> str:
    """Normalise a UUID value (str or uuid.UUID) to the dash-less hex form used
    by the domain model."""

    return _uuid.UUID(str(value)).hex


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class PostGISGeospatialRepository:
    """Production repository backed by PostGIS (migration 0006).

    Fail-closed: refuses to construct without the psycopg driver and an
    explicit DSN (``GEOSPATIAL_POSTGIS_DSN``). Every connection is pinned to
    exactly one tenant: on acquisition the session variable
    ``app.current_state_tenant`` is set so the RLS policies in
    ``db/migrations/0006_geospatial.sql`` enforce tenant isolation at the
    database layer; cross-tenant reads therefore return no rows and surface
    as ``NotFoundError``.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        *,
        connect: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._dsn = dsn or os.environ.get(POSTGIS_DSN_ENV)
        if not self._dsn:
            raise AdapterUnavailableError(
                f"PostGISGeospatialRepository requires {POSTGIS_DSN_ENV}; refusing to run unconfigured"
            )
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise AdapterUnavailableError(
                "PostGISGeospatialRepository requires the optional 'psycopg' package"
            ) from exc
        if connect is None:  # pragma: no cover - production path
            import psycopg as _psycopg

            connect = _psycopg.connect
        self._connect = connect
        self._lock = threading.Lock()
        self._conns: dict[str, Any] = {}  # tenant_state_id -> pinned connection

    # -- connection / RLS -----------------------------------------------------
    def _conn(self, tenant_state_id: str) -> Any:
        """Return a connection pinned to ``tenant_state_id`` via RLS."""

        with self._lock:
            conn = self._conns.get(tenant_state_id)
            if conn is None:
                conn = self._connect(self._dsn)
                with conn.cursor() as cur:
                    # Session-level RLS pin: every subsequent statement on this
                    # connection is scoped to this tenant by the RLS policies.
                    cur.execute(
                        "SELECT set_config('app.current_state_tenant', %s, false)",
                        (tenant_state_id,),
                    )
                conn.commit()
                self._conns[tenant_state_id] = conn
            return conn

    def close(self) -> None:
        with self._lock:
            for conn in self._conns.values():
                conn.close()
            self._conns.clear()

    @staticmethod
    def _jsonb(value: Any) -> Any:
        from psycopg.types.json import Jsonb

        return Jsonb(value)

    # -- datasets -------------------------------------------------------------
    def save_dataset(self, dataset: Dataset) -> Dataset:
        conn = self._conn(dataset.tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geospatial.datasets
                    (dataset_id, tenant_state_id, dataset_type, name, source_uri,
                     crs, geometry_metadata, sensitivity, h3_resolution)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dataset_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    source_uri = EXCLUDED.source_uri,
                    geometry_metadata = EXCLUDED.geometry_metadata,
                    sensitivity = EXCLUDED.sensitivity,
                    h3_resolution = EXCLUDED.h3_resolution
                """,
                (
                    dataset.dataset_id,
                    dataset.tenant_state_id,
                    dataset.dataset_type.value,
                    dataset.name,
                    dataset.source_uri,
                    dataset.crs,
                    self._jsonb(dataset.geometry_metadata),
                    dataset.sensitivity.value,
                    dataset.h3_resolution,
                ),
            )
        conn.commit()
        return dataset

    def get_dataset(self, tenant_state_id: str, dataset_id: str) -> Dataset:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_id, tenant_state_id, dataset_type, name, source_uri,
                       crs, geometry_metadata, sensitivity, h3_resolution, created_at
                FROM geospatial.datasets WHERE dataset_id = %s
                """,
                (dataset_id,),
            )
            row = cur.fetchone()
        if row is None:
            # Absent rows include cross-tenant rows hidden by RLS.
            raise NotFoundError(dataset_id)
        return self._dataset_from_row(row)

    def list_datasets(self, tenant_state_id: str) -> list[Dataset]:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset_id, tenant_state_id, dataset_type, name, source_uri,
                       crs, geometry_metadata, sensitivity, h3_resolution, created_at
                FROM geospatial.datasets ORDER BY created_at
                """
            )
            return [self._dataset_from_row(r) for r in cur.fetchall()]

    @staticmethod
    def _dataset_from_row(row: Any) -> Dataset:
        return Dataset(
            dataset_id=_uuid_str(row[0]),
            tenant_state_id=row[1],
            dataset_type=DatasetType(row[2]),
            name=row[3],
            source_uri=row[4],
            crs=row[5],
            geometry_metadata=dict(row[6] or {}),
            sensitivity=Sensitivity(row[7]),
            h3_resolution=row[8] if row[8] is not None else 9,
            created_at=_iso(row[9]),
        )

    # -- jobs -----------------------------------------------------------------
    def save_job(self, job: ProcessingJob) -> ProcessingJob:
        conn = self._conn(job.tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geospatial.processing_jobs
                    (job_id, tenant_state_id, job_type, status, input_dataset_ids,
                     parameters, output_uri, error, started_at, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (job_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    output_uri = EXCLUDED.output_uri,
                    error = EXCLUDED.error,
                    started_at = EXCLUDED.started_at,
                    completed_at = EXCLUDED.completed_at
                """,
                (
                    job.job_id,
                    job.tenant_state_id,
                    job.job_type.value,
                    job.status.value,
                    list(job.input_dataset_ids),
                    self._jsonb(job.parameters),
                    job.output_uri,
                    job.error,
                    job.started_at or None,
                    job.completed_at or None,
                ),
            )
        conn.commit()
        return job

    def get_job(self, tenant_state_id: str, job_id: str) -> ProcessingJob:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, tenant_state_id, job_type, status, input_dataset_ids,
                       parameters, output_uri, error, created_at, started_at, completed_at
                FROM geospatial.processing_jobs WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise NotFoundError(job_id)
        return self._job_from_row(row)

    def list_jobs(self, tenant_state_id: str) -> list[ProcessingJob]:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, tenant_state_id, job_type, status, input_dataset_ids,
                       parameters, output_uri, error, created_at, started_at, completed_at
                FROM geospatial.processing_jobs ORDER BY created_at
                """
            )
            return [self._job_from_row(r) for r in cur.fetchall()]

    @staticmethod
    def _job_from_row(row: Any) -> ProcessingJob:
        return ProcessingJob(
            job_id=_uuid_str(row[0]),
            tenant_state_id=row[1],
            job_type=JobType(row[2]),
            status=JobStatus(row[3]),
            input_dataset_ids=[_uuid_str(v) for v in (row[4] or [])],
            parameters=dict(row[5] or {}),
            output_uri=row[6],
            error=row[7],
            created_at=_iso(row[8]),
            started_at=_iso(row[9]) or None,
            completed_at=_iso(row[10]) or None,
        )

    # -- results ----------------------------------------------------------------
    def save_result(self, result: JobResult) -> JobResult:
        conn = self._conn(result.tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geospatial.job_results
                    (result_id, job_id, tenant_state_id, result_type, result_uri,
                     metrics, result_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result.result_id,
                    result.job_id,
                    result.tenant_state_id,
                    result.result_type,
                    result.result_uri,
                    self._jsonb(result.metrics),
                    result.result_hash,
                ),
            )
        conn.commit()
        return result

    def list_results(self, tenant_state_id: str, job_id: str) -> list[JobResult]:
        self.get_job(tenant_state_id, job_id)  # enforces tenant scope (RLS)
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT result_id, job_id, tenant_state_id, result_type, result_uri,
                       metrics, result_hash, created_at
                FROM geospatial.job_results WHERE job_id = %s ORDER BY created_at
                """,
                (job_id,),
            )
            return [
                JobResult(
                    result_id=_uuid_str(r[0]),
                    job_id=_uuid_str(r[1]),
                    tenant_state_id=r[2],
                    result_type=r[3],
                    result_uri=r[4],
                    metrics=dict(r[5] or {}),
                    result_hash=r[6],
                    created_at=_iso(r[7]),
                )
                for r in cur.fetchall()
            ]

    # -- geolibre projects ------------------------------------------------------
    def save_project(self, project: GeolibreProject) -> GeolibreProject:
        conn = self._conn(project.tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geospatial.geolibre_projects
                    (project_id, tenant_state_id, name, project_uri, project_hash,
                     redaction_level, source_job_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    project_uri = EXCLUDED.project_uri,
                    project_hash = EXCLUDED.project_hash,
                    redaction_level = EXCLUDED.redaction_level
                """,
                (
                    project.project_id,
                    project.tenant_state_id,
                    project.name,
                    project.project_uri,
                    project.project_hash,
                    project.redaction_level,
                    project.source_job_id,
                ),
            )
        conn.commit()
        return project

    def get_project(self, tenant_state_id: str, project_id: str) -> GeolibreProject:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT project_id, tenant_state_id, name, project_uri, project_hash,
                       redaction_level, source_job_id, created_at
                FROM geospatial.geolibre_projects WHERE project_id = %s
                """,
                (project_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise NotFoundError(project_id)
        return GeolibreProject(
            project_id=_uuid_str(row[0]),
            tenant_state_id=row[1],
            name=row[2],
            project_uri=row[3],
            project_hash=row[4],
            redaction_level=row[5],
            source_job_id=_uuid_str(row[6]) if row[6] else None,
            created_at=_iso(row[7]),
        )

    # -- audit --------------------------------------------------------------------
    def append_audit(self, entry: AuditEntry) -> AuditEntry:
        conn = self._conn(entry.tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geospatial.audit_log
                    (sequence, tenant_state_id, action, resource_type, resource_id,
                     payload_hash, object_uri, prev_hash, entry_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.sequence,
                    entry.tenant_state_id,
                    entry.action,
                    entry.resource_type,
                    entry.resource_id,
                    entry.payload_hash,
                    entry.object_uri,
                    entry.prev_hash,
                    entry.entry_hash,
                ),
            )
        conn.commit()
        return entry

    def list_audit(self, tenant_state_id: str) -> list[AuditEntry]:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sequence, tenant_state_id, action, resource_type, resource_id,
                       payload_hash, object_uri, prev_hash, entry_hash, created_at
                FROM geospatial.audit_log ORDER BY sequence
                """
            )
            return [
                AuditEntry(
                    sequence=r[0],
                    tenant_state_id=r[1],
                    action=r[2],
                    resource_type=r[3],
                    resource_id=r[4],
                    payload_hash=r[5],
                    object_uri=r[6],
                    prev_hash=r[7],
                    entry_hash=r[8],
                    created_at=_iso(r[9]),
                )
                for r in cur.fetchall()
            ]

    def last_audit_hash(self, tenant_state_id: str) -> str:
        conn = self._conn(tenant_state_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entry_hash FROM geospatial.audit_log
                ORDER BY sequence DESC LIMIT 1
                """
            )
            row = cur.fetchone()
        return row[0] if row else "GENESIS"
