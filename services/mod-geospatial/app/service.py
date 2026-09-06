"""Service layer: tenant-scoped orchestration of datasets, jobs, H3 indexing,
GeoLibre projects, lakehouse publication, and hash-only audit.

Local job execution reuses the existing tested runners in
``geospatial/local/`` (unassessed property join, NDVI change detection)
without modifying them.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .adapters.geolibre_adapter import build_layer, get_geolibre_builder
from .adapters.h3_adapter import get_h3_adapter
from .adapters.lakehouse_adapter import LakehouseAdapter
from .domain import (
    AuditEntry,
    Dataset,
    DatasetType,
    GeolibreProject,
    JobResult,
    JobStatus,
    JobType,
    ProcessingJob,
    Sensitivity,
    utc_now_iso,
)
from .geometry import canonical_json, feature_collection_from_parameters, geometry_hash, sha256_hex

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GEOSPATIAL_DIR = _REPO_ROOT / "geospatial"

_NS = uuid.UUID("3f7a2c10-6e1d-4f2a-9b7c-5b2e9c8d6a11")


def _load_local_runners():
    """Import the existing tested local geospatial runners (read-only reuse)."""

    geospatial_dir = Path(os.environ.get("GEOSPATIAL_LOCAL_DIR", str(_GEOSPATIAL_DIR)))
    if str(geospatial_dir) not in sys.path:
        sys.path.insert(0, str(geospatial_dir))
    from local import ndvi_change_detection, unassessed_property_join

    return unassessed_property_join, ndvi_change_detection


class GeospatialService:
    def __init__(
        self,
        repository,
        *,
        output_dir: Optional[str] = None,
        h3_adapter=None,
        geolibre_builder=None,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.repo = repository
        self.clock = clock
        self.output_dir = output_dir or os.environ.get(
            "GEOSPATIAL_OUTPUT_DIR", str(_REPO_ROOT / "services" / "mod-geospatial" / "var")
        )
        self.h3 = h3_adapter or get_h3_adapter(prefer_real=False)
        self.geolibre_builder = geolibre_builder or get_geolibre_builder(prefer_package=False)
        self.lakehouse = LakehouseAdapter(self.output_dir)

    # -- audit --------------------------------------------------------------
    def _audit(
        self,
        tenant_state_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        payload: dict[str, Any],
        object_uri: Optional[str] = None,
    ) -> AuditEntry:
        """Append a hash-only, hash-chained audit entry. The payload is hashed;
        raw geometry/PII must never be passed in ``payload``."""

        prev_hash = self.repo.last_audit_hash(tenant_state_id)
        payload_hash = sha256_hex(canonical_json(payload))
        sequence = len(self.repo.list_audit(tenant_state_id)) + 1
        entry_hash = sha256_hex(
            canonical_json(
                {
                    "sequence": sequence,
                    "tenant_state_id": tenant_state_id,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "payload_hash": payload_hash,
                    "object_uri": object_uri,
                    "prev_hash": prev_hash,
                }
            )
        )
        entry = AuditEntry(
            sequence=sequence,
            tenant_state_id=tenant_state_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload_hash=payload_hash,
            object_uri=object_uri,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            created_at=self.clock(),
        )
        return self.repo.append_audit(entry)

    # -- datasets -------------------------------------------------------------
    def register_dataset(self, tenant_state_id: str, request: dict[str, Any]) -> Dataset:
        geometry = request.get("geometry")
        geom_meta: dict[str, Any] = {}
        if geometry is not None:
            # Validate first (raises GeometryError on bad GeoJSON). Hash-only:
            # validated geometry contributes a hash, never raw coords, when the
            # dataset is marked SENSITIVE.
            from .geometry import parse_geojson_geometry

            parse_geojson_geometry(geometry)
            geom_hash = geometry_hash(geometry)
            geom_meta = {"geometry_sha256": geom_hash, "geometry_type": geometry.get("type")}
            if request.get("sensitivity", Sensitivity.INTERNAL.value) != Sensitivity.SENSITIVE.value:
                geom_meta["bbox"] = request.get("bbox")

        dataset_id = uuid.uuid5(
            _NS, f"dataset:{tenant_state_id}:{request['dataset_type']}:{request['name']}"
        ).hex
        dataset = Dataset(
            dataset_id=dataset_id,
            tenant_state_id=tenant_state_id,
            dataset_type=DatasetType(request["dataset_type"]),
            name=request["name"],
            source_uri=request["source_uri"],
            crs=request.get("crs", "EPSG:4326"),
            geometry_metadata=geom_meta,
            sensitivity=Sensitivity(request.get("sensitivity", Sensitivity.INTERNAL.value)),
            h3_resolution=int(request.get("h3_resolution", 9)),
            created_at=self.clock(),
        )
        self.repo.save_dataset(dataset)
        self._audit(
            tenant_state_id,
            "dataset_registered",
            "dataset",
            dataset.dataset_id,
            {
                "dataset_type": dataset.dataset_type.value,
                "name": dataset.name,
                "sensitivity": dataset.sensitivity.value,
                "geometry_metadata": dataset.geometry_metadata,
            },
            object_uri=dataset.source_uri,
        )
        return dataset

    # -- jobs -----------------------------------------------------------------
    def create_job(self, tenant_state_id: str, request: dict[str, Any]) -> ProcessingJob:
        job_type = JobType(request["job_type"])
        input_ids = list(request.get("input_dataset_ids", []))
        for ds_id in input_ids:  # enforce tenant scope of referenced datasets
            self.repo.get_dataset(tenant_state_id, ds_id)
        job_id = uuid.uuid4().hex
        job = ProcessingJob(
            job_id=job_id,
            tenant_state_id=tenant_state_id,
            job_type=job_type,
            input_dataset_ids=input_ids,
            parameters=dict(request.get("parameters", {})),
            created_at=self.clock(),
        )
        self.repo.save_job(job)
        self._audit(
            tenant_state_id,
            "job_created",
            "processing_job",
            job.job_id,
            {"job_type": job.job_type.value, "input_dataset_ids": input_ids},
        )
        return job

    def run_job(self, tenant_state_id: str, job_id: str) -> ProcessingJob:
        """Deterministic local execution of a queued job."""

        job = self.repo.get_job(tenant_state_id, job_id)
        job.transition(JobStatus.RUNNING, self.clock())
        self.repo.save_job(job)
        try:
            result = self._execute(job)
        except Exception as exc:  # noqa: BLE001 - failure recorded on the job
            job.error = str(exc)
            job.transition(JobStatus.FAILED, self.clock())
            self.repo.save_job(job)
            self._audit(
                tenant_state_id, "job_failed", "processing_job", job.job_id, {"error": str(exc)}
            )
            return job
        job.output_uri = result.result_uri
        job.transition(JobStatus.SUCCEEDED, self.clock())
        self.repo.save_job(job)
        self.repo.save_result(result)
        self._audit(
            tenant_state_id,
            "job_completed",
            "processing_job",
            job.job_id,
            {"job_type": job.job_type.value, "result_hash": result.result_hash, "metrics": result.metrics},
            object_uri=result.result_uri,
        )
        return job

    # -- job implementations (deterministic local) ------------------------------
    def _execute(self, job: ProcessingJob) -> JobResult:
        handler = {
            JobType.UNASSESSED_PROPERTY_JOIN: self._exec_unassessed_property_join,
            JobType.NDVI_CHANGE_DETECTION: self._exec_ndvi_change_detection,
            JobType.H3_AGGREGATION: self._exec_h3_aggregation,
            JobType.GEOPARQUET_EXPORT: self._exec_geoparquet_export,
            JobType.GEOLIBRE_PROJECT_BUILD: self._exec_geolibre_project_build,
        }[job.job_type]
        result_type, metrics, uri = handler(job)
        result = JobResult(
            result_id=uuid.uuid5(_NS, f"result:{job.job_id}").hex,
            job_id=job.job_id,
            tenant_state_id=job.tenant_state_id,
            result_type=result_type,
            result_uri=uri,
            metrics=metrics,
            result_hash=sha256_hex(canonical_json({"result_type": result_type, "metrics": metrics, "uri": uri})),
            created_at=self.clock(),
        )
        return result

    def _exec_unassessed_property_join(self, job: ProcessingJob) -> tuple[str, dict, Optional[str]]:
        """Reuse ``geospatial.local.unassessed_property_join`` semantics."""

        upj, _ = _load_local_runners()
        from shapely.geometry import shape

        params = job.parameters
        buildings = [
            upj.FootprintFeature(
                building_footprint_id=props["building_footprint_id"],
                tenant_state_id=props["tenant_state_id"],
                estimated_area_sqm=float(props["estimated_area_sqm"]),
                geom=geom,
            )
            for props, geom in feature_collection_from_parameters(params.get("buildings", []))
        ]
        parcels = [
            upj.ParcelFeature(
                parcel_uin=props["parcel_uin"],
                tenant_state_id=props["tenant_state_id"],
                owner_stin=props.get("owner_stin", ""),
                assessed_annual_luc_kobo=int(props.get("assessed_annual_luc_kobo", 0)),
                geom=geom,
            )
            for props, geom in feature_collection_from_parameters(params.get("parcels", []))
        ]
        rows = upj.run_unassessed_property_join(buildings, parcels, job.tenant_state_id)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.audit_status.value] = counts.get(row.audit_status.value, 0) + 1
        metrics = {"rows": len(rows), "status_counts": counts, "backend": "geospatial.local"}
        return "UNASSESSED_PROPERTY_JOIN_RESULT", metrics, None

    def _exec_ndvi_change_detection(self, job: ProcessingJob) -> tuple[str, dict, Optional[str]]:
        """Reuse ``geospatial.local.ndvi_change_detection``; alerts get H3 cells."""

        import numpy as np

        _, ndvi = _load_local_runners()
        params = job.parameters
        b04_t0 = np.array(params["b04_t0"], dtype=float)
        b08_t0 = np.array(params["b08_t0"], dtype=float)
        b04_t1 = np.array(params["b04_t1"], dtype=float)
        b08_t1 = np.array(params["b08_t1"], dtype=float)
        delta = ndvi.compute_ndvi_delta_local(
            ndvi.compute_ndvi(b04_t0, b08_t0), ndvi.compute_ndvi(b04_t1, b08_t1)
        )
        resolution = int(params.get("h3_resolution", 9))
        alerts = ndvi.detect_disturbance_local(
            delta,
            state_id=job.tenant_state_id,
            scene_id=params.get("scene_id", "local-scene"),
            origin_lat=float(params.get("origin_lat", 9.0)),
            origin_lon=float(params.get("origin_lon", 8.0)),
            min_area_ha=float(params.get("min_area_ha", ndvi.MIN_DISTURBANCE_HA)),
        )
        summaries = [
            {
                "scene_id": a.scene_id,
                "disturbance_ha": a.disturbance_ha,
                "suspected_activity": a.suspected_activity.value,
                "h3_cell": self.h3.cell_for_point(a.centroid_lat, a.centroid_lon, resolution),
            }
            for a in alerts
        ]
        metrics = {
            "alerts": summaries,
            "alert_count": len(summaries),
            "h3_backend": self.h3.backend_name,
            "backend": "geospatial.local",
        }
        return "NDVI_ALERT_SUMMARY", metrics, None

    def _exec_h3_aggregation(self, job: ProcessingJob) -> tuple[str, dict, Optional[str]]:
        resolution = int(job.parameters.get("resolution", 9))
        features = feature_collection_from_parameters(job.parameters.get("features", []))
        counts: dict[str, int] = {}
        for _props, geom in features:
            for cell in self.h3.cells_for_geometry(geom.__geo_interface__, resolution):
                counts[cell] = counts.get(cell, 0) + 1
        metrics = {
            "resolution": resolution,
            "cell_count": len(counts),
            "cells": dict(sorted(counts.items())),
            "h3_backend": self.h3.backend_name,
        }
        return "H3_AGGREGATION_RESULT", metrics, None

    def _exec_geoparquet_export(self, job: ProcessingJob) -> tuple[str, dict, Optional[str]]:
        features = job.parameters.get("features", [])
        feature_collection_from_parameters(features)  # validate geometries
        manifest = self.lakehouse.export_features(
            features,
            destination=f"export-{job.job_id}",
            crs=job.parameters.get("crs", "EPSG:4326"),
        )
        return "GEOPARQUET_EXPORT_MANIFEST", manifest, manifest["uri"]

    def _exec_geolibre_project_build(self, job: ProcessingJob) -> tuple[str, dict, Optional[str]]:
        params = job.parameters
        project = self.build_project(
            job.tenant_state_id,
            name=params.get("name", f"job-{job.job_id}"),
            dataset_ids=params.get("dataset_ids", job.input_dataset_ids),
            source_job_id=job.job_id,
        )
        return "GEOLIBRE_PROJECT", {"project_id": project.project_id, "project_hash": project.project_hash}, project.project_uri

    # -- h3 ---------------------------------------------------------------------
    def h3_index(self, tenant_state_id: str, geometry: dict[str, Any], resolution: int) -> dict[str, Any]:
        from .geometry import parse_geojson_geometry

        geom = parse_geojson_geometry(geometry)
        cells = self.h3.cells_for_geometry(geom.__geo_interface__, resolution)
        self._audit(
            tenant_state_id,
            "h3_indexed",
            "geometry",
            geometry_hash(geometry),
            {"resolution": resolution, "cell_count": len(cells), "h3_backend": self.h3.backend_name},
        )
        return {"h3_backend": self.h3.backend_name, "resolution": resolution, "cells": cells}

    # -- geolibre projects ------------------------------------------------------
    def build_project(
        self,
        tenant_state_id: str,
        *,
        name: str,
        dataset_ids: list[str],
        source_job_id: Optional[str] = None,
    ) -> GeolibreProject:
        layers = []
        redaction_level = "NONE"
        for ds_id in dataset_ids:
            ds = self.repo.get_dataset(tenant_state_id, ds_id)
            sensitive = ds.sensitivity is Sensitivity.SENSITIVE
            if sensitive:
                redaction_level = "SENSITIVE"
            layers.append(
                build_layer(
                    layer_id=ds.dataset_id,
                    title=ds.name,
                    source_url=ds.source_uri,
                    layer_type="vector",
                    sensitive=sensitive,
                    geometry_hash=ds.geometry_metadata.get("geometry_sha256"),
                )
            )
        doc = self.geolibre_builder.build_project(name=name, layers=layers, redaction_level=redaction_level)
        blob = canonical_json(doc)
        os.makedirs(self.output_dir, exist_ok=True)
        uri = os.path.join(self.output_dir, f"project-{uuid.uuid5(_NS, blob).hex}.geolibre.json")
        with open(uri, "w", encoding="utf-8") as fh:
            fh.write(blob)
        project = GeolibreProject(
            project_id=uuid.uuid5(_NS, f"project:{tenant_state_id}:{blob}").hex,
            tenant_state_id=tenant_state_id,
            name=name,
            project_uri=uri,
            project_hash=doc["project_hash"],
            redaction_level=redaction_level,
            source_job_id=source_job_id,
            created_at=self.clock(),
        )
        self.repo.save_project(project)
        self._audit(
            tenant_state_id,
            "geolibre_project_built",
            "geolibre_project",
            project.project_id,
            {"name": name, "redaction_level": redaction_level, "project_hash": project.project_hash},
            object_uri=uri,
        )
        return project
