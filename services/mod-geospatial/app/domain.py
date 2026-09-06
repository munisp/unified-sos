"""Domain model for mod-geospatial.

Pure dataclasses + enums; no framework or IO dependencies so the domain layer
stays deterministic and unit-testable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class DatasetType(str, enum.Enum):
    CADASTRE = "CADASTRE"
    BUILDING_FOOTPRINTS = "BUILDING_FOOTPRINTS"
    NDVI_ALERTS = "NDVI_ALERTS"
    FOREST_RESERVES = "FOREST_RESERVES"
    TRANSPORT_CORRIDORS = "TRANSPORT_CORRIDORS"
    MARKET_BOUNDARIES = "MARKET_BOUNDARIES"
    MINING_SITES = "MINING_SITES"


class Sensitivity(str, enum.Enum):
    """Sensitivity governs audit redaction: SENSITIVE datasets never emit raw
    geometry or landowner attributes into audit entries or project payloads."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"


class JobType(str, enum.Enum):
    UNASSESSED_PROPERTY_JOIN = "UNASSESSED_PROPERTY_JOIN"
    NDVI_CHANGE_DETECTION = "NDVI_CHANGE_DETECTION"
    H3_AGGREGATION = "H3_AGGREGATION"
    GEOPARQUET_EXPORT = "GEOPARQUET_EXPORT"
    GEOLIBRE_PROJECT_BUILD = "GEOLIBRE_PROJECT_BUILD"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


#: Legal state-machine transitions for processing jobs.
JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


class JobTransitionError(ValueError):
    """Raised when an illegal job status transition is attempted."""


class TenantIsolationError(PermissionError):
    """Raised when a record is accessed under the wrong tenant_state_id."""


class NotFoundError(KeyError):
    """Raised when a record does not exist for the tenant."""


class AdapterUnavailableError(RuntimeError):
    """Fail-closed error raised by production adapter seams when credentials,
    endpoints, binaries, or optional packages are unavailable."""


@dataclass
class Dataset:
    dataset_id: str
    tenant_state_id: str
    dataset_type: DatasetType
    name: str
    source_uri: str
    crs: str = "EPSG:4326"
    geometry_metadata: dict[str, Any] = field(default_factory=dict)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    h3_resolution: int = 9
    created_at: str = ""


@dataclass
class ProcessingJob:
    job_id: str
    tenant_state_id: str
    job_type: JobType
    status: JobStatus = JobStatus.QUEUED
    input_dataset_ids: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    output_uri: Optional[str] = None
    error: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def transition(self, target: JobStatus, at: str) -> None:
        if target not in JOB_TRANSITIONS[self.status]:
            raise JobTransitionError(f"illegal job transition {self.status.value} -> {target.value}")
        self.status = target
        if target is JobStatus.RUNNING:
            self.started_at = at
        if target in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            self.completed_at = at


@dataclass
class JobResult:
    result_id: str
    job_id: str
    tenant_state_id: str
    result_type: str
    result_uri: Optional[str]
    metrics: dict[str, Any]
    result_hash: str
    created_at: str


@dataclass
class GeolibreProject:
    project_id: str
    tenant_state_id: str
    name: str
    project_uri: str
    project_hash: str
    redaction_level: str
    source_job_id: Optional[str] = None
    created_at: str = ""


@dataclass
class AuditEntry:
    """Hash-only, hash-chained audit record.

    ``payload_hash`` is the SHA-256 of the canonical payload; ``entry_hash``
    chains to the previous entry so tampering is detectable. Raw geometry and
    PII never appear here — only hashes, IDs, and object URIs.
    """

    sequence: int
    tenant_state_id: str
    action: str
    resource_type: str
    resource_id: str
    payload_hash: str
    object_uri: Optional[str]
    prev_hash: str
    entry_hash: str
    created_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
