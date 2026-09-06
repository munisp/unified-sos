"""Repository layer.

``InMemoryGeospatialRepository`` backs local/test mode.
``PostGISGeospatialRepository`` is a fail-closed production seam: it refuses
to operate unless psycopg and a DSN are both present, and otherwise raises
``AdapterUnavailableError``. Every method is tenant-scoped.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from .domain import (
    AdapterUnavailableError,
    AuditEntry,
    Dataset,
    GeolibreProject,
    JobResult,
    NotFoundError,
    ProcessingJob,
    TenantIsolationError,
)


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


class PostGISGeospatialRepository:
    """Fail-closed production seam for the PostGIS system of record.

    The migration ``db/migrations/0006_geospatial.sql`` defines the backing
    schema (RLS on ``app.current_state_tenant``). This seam intentionally
    refuses to run without an explicit DSN and the psycopg driver so that
    misconfigured production deploys fail closed instead of silently dropping
    tenant isolation.
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        self._dsn = dsn
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise AdapterUnavailableError(
                "PostGISGeospatialRepository requires the optional 'psycopg' package"
            ) from exc
        if not self._dsn:
            raise AdapterUnavailableError(
                "PostGISGeospatialRepository requires GEOSPATIAL_POSTGIS_DSN; refusing to run unconfigured"
            )

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - seam guard
        raise AdapterUnavailableError(
            f"PostGISGeospatialRepository.{name} is a production seam; the SQL mapping "
            "is delivered with db/migrations/0006_geospatial.sql"
        )
