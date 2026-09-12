"""Parcel subdivision & merger with area conservation and lineage tracking.

* **Subdivision** — one parent parcel → N child parcels. The geodesic areas
  of the children must sum to the parent's geodesic area within
  :data:`AREA_CONSERVATION_TOLERANCE` (0.5%); the parent is marked
  ``SUPERSEDED`` (never deleted — chain-of-title evidence is retained) and
  each child records ``parent_parcel_ids`` lineage.
* **Merger** — the inverse: 2+ *adjacent* parents → one child parcel, same
  area-conservation rule, parents SUPERSEDED.

Both operations are only allowed on parcels with status ``ACTIVE`` or
``REGISTERED``, with **no open dispute** (:class:`~.disputes.DisputeGuard`)
and **no RUNNING titling workflow**. Every mutation is recorded in the
hash-chained cadastre event log (:mod:`lands_app.eventlog`).
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from shapely.geometry import shape
from shapely.ops import unary_union

from .disputes import DisputeGuard
from .eventlog import CadastreEventLog
from .geometry import GeometryError, area_sqm, parse_boundary
from .repository import ParcelRepository
from .schemas import ParcelRecord, ParcelStatus
from .titling import LocalTitlingRunner, WorkflowError, WorkflowStatus

#: Accepted relative deviation between the sum of child areas and the parent
#: area (subdivision), or between the merged child area and the sum of parent
#: areas (merger). Survey-grade tolerance: 0.5%.
AREA_CONSERVATION_TOLERANCE = 0.005

#: Statuses eligible for subdivision/merger.
ELIGIBLE_STATUSES = (ParcelStatus.ACTIVE, ParcelStatus.REGISTERED)


class SubdivisionError(Exception):
    """Eligibility or lifecycle violation (mapped to HTTP 409)."""


class AreaConservationError(ValueError):
    """Child/parent area mismatch beyond tolerance (mapped to HTTP 422)."""


class ChildParcelSpec(BaseModel):
    """Boundary + identity of one child parcel produced by subdivision/merger."""

    parcel_uin: str = Field(description="Unique Identification Number for the child parcel")
    boundary_geojson: dict[str, Any] = Field(description="GeoJSON Polygon, WGS84 (EPSG:4326)")
    owner_stin: Optional[str] = Field(
        default=None, description="Defaults to the parent's owner when omitted"
    )
    survey_plan_no: Optional[str] = None


class SubdivisionService:
    """Orchestrates subdivision/merger against repo + workflow + guard."""

    def __init__(
        self,
        repository: ParcelRepository,
        titling_runner: LocalTitlingRunner,
        event_log: CadastreEventLog,
        dispute_guard: DisputeGuard,
    ) -> None:
        self._repo = repository
        self._runner = titling_runner
        self._log = event_log
        self._guard = dispute_guard

    # -- eligibility ---------------------------------------------------------
    def _assert_eligible(self, record: ParcelRecord) -> None:
        if record.status not in ELIGIBLE_STATUSES:
            raise SubdivisionError(
                f"parcel {record.parcel_id} has status {record.status.value}; "
                "only ACTIVE/REGISTERED parcels can be subdivided or merged"
            )
        # DisputeGuard: contested titles are frozen (HTTP 409 at the API).
        self._guard.assert_clear(record.tenant_state_id, record.parcel_id)
        # No RUNNING titling workflow on the parcel.
        if record.titling_workflow_id:
            try:
                instance = self._runner.get(record.titling_workflow_id)
            except WorkflowError:
                instance = None
            if instance is not None and instance.status == WorkflowStatus.RUNNING:
                raise SubdivisionError(
                    f"parcel {record.parcel_id} has titling workflow "
                    f"{record.titling_workflow_id} RUNNING; wait for issuance "
                    "or rejection before subdividing/merging"
                )

    @staticmethod
    def _check_area_conservation(child_areas: list[float], parent_area: float) -> None:
        total = sum(child_areas)
        if parent_area <= 0:
            raise AreaConservationError("parent parcel has zero geodesic area")
        if abs(total - parent_area) / parent_area > AREA_CONSERVATION_TOLERANCE:
            raise AreaConservationError(
                f"child areas sum to {total:.2f} m² but parent area is "
                f"{parent_area:.2f} m² — deviation exceeds "
                f"{AREA_CONSERVATION_TOLERANCE:.1%} (area conservation)"
            )

    def _make_child(
        self,
        spec: ChildParcelSpec,
        *,
        tenant_state_id: str,
        lga_id: str,
        land_use_type,
        fallback_owner: str,
        fallback_survey_plan: str,
        title_type,
        parents: list[ParcelRecord],
    ) -> tuple[ParcelRecord, float]:
        try:
            geom = parse_boundary(spec.boundary_geojson)
        except GeometryError as exc:
            raise AreaConservationError(f"invalid child boundary: {exc}") from exc
        child_area = area_sqm(geom)
        child = ParcelRecord(
            parcel_id=uuid4(),
            tenant_state_id=tenant_state_id,
            lga_id=lga_id,
            parcel_uin=spec.parcel_uin,
            owner_stin=spec.owner_stin or fallback_owner,
            land_use_type=land_use_type,
            survey_plan_no=spec.survey_plan_no or fallback_survey_plan,
            beacon_count=max(3, len(spec.boundary_geojson["coordinates"][0]) - 1),
            area_sqm=round(child_area, 2),
            title_type=title_type,
            status=ParcelStatus.REGISTERED,
            boundary_geojson=spec.boundary_geojson,
            titling_workflow_id="",
            parent_parcel_ids=[p.parcel_id for p in parents],
        )
        return child, child_area

    # -- subdivision -----------------------------------------------------------
    def subdivide(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        children: list[ChildParcelSpec],
        *,
        actor: str = "lands-registry",
    ) -> tuple[ParcelRecord, list[ParcelRecord]]:
        """Split ``parcel_id`` into N child parcels (parent → SUPERSEDED)."""
        parent = self._repo.get(tenant_state_id, parcel_id)
        if parent is None:
            raise SubdivisionError(f"parcel {parcel_id} not found")
        if len(children) < 2:
            raise AreaConservationError("subdivision requires at least 2 child parcels")
        self._assert_eligible(parent)

        parent_area = area_sqm(shape(parent.boundary_geojson))
        child_records: list[ParcelRecord] = []
        child_areas: list[float] = []
        for spec in children:
            child, child_area = self._make_child(
                spec,
                tenant_state_id=tenant_state_id,
                lga_id=parent.lga_id,
                land_use_type=parent.land_use_type,
                fallback_owner=parent.owner_stin,
                fallback_survey_plan=parent.survey_plan_no,
                title_type=parent.title_type,
                parents=[parent],
            )
            child_records.append(child)
            child_areas.append(child_area)
        self._check_area_conservation(child_areas, parent_area)

        superseded = parent.model_copy(update={"status": ParcelStatus.SUPERSEDED})
        self._repo.update(superseded)
        for child in child_records:
            self._repo.add(child)  # DuplicateParcelError propagates → 409
        self._log.record(
            "PARCEL_SUBDIVIDED",
            tenant_state_id,
            parcel_id=parent.parcel_id,
            actor=actor,
            detail={
                "parent_parcel_uin": parent.parcel_uin,
                "parent_area_sqm": round(parent_area, 2),
                "children": [
                    {"parcel_id": str(c.parcel_id), "parcel_uin": c.parcel_uin,
                     "area_sqm": c.area_sqm}
                    for c in child_records
                ],
            },
        )
        for child in child_records:
            self._log.record(
                "PARCEL_CREATED_FROM_SUBDIVISION",
                tenant_state_id,
                parcel_id=child.parcel_id,
                actor=actor,
                detail={"parcel_uin": child.parcel_uin,
                        "parent_parcel_id": str(parent.parcel_id)},
            )
        return superseded, child_records

    # -- merger ------------------------------------------------------------------
    def merge(
        self,
        tenant_state_id: str,
        parent_parcel_ids: list[UUID],
        child: ChildParcelSpec,
        *,
        actor: str = "lands-registry",
    ) -> tuple[list[ParcelRecord], ParcelRecord]:
        """Merge 2+ adjacent parents into one child parcel (parents → SUPERSEDED)."""
        if len(parent_parcel_ids) < 2:
            raise AreaConservationError("merger requires at least 2 parent parcels")
        parents: list[ParcelRecord] = []
        for pid in parent_parcel_ids:
            record = self._repo.get(tenant_state_id, pid)
            if record is None:
                raise SubdivisionError(f"parcel {pid} not found")
            parents.append(record)
        for record in parents:
            self._assert_eligible(record)

        # Adjacency: the union of parent boundaries must be a single
        # connected polygon (MultiPolygon ⇒ non-contiguous parents).
        parent_geoms = [shape(p.boundary_geojson) for p in parents]
        union = unary_union(parent_geoms)
        if union.geom_type != "Polygon":
            raise AreaConservationError(
                "parent parcels are not contiguous — their union is a "
                f"{union.geom_type}, merger requires adjacency"
            )

        parent_areas = [area_sqm(g) for g in parent_geoms]
        child_record, child_area = self._make_child(
            child,
            tenant_state_id=tenant_state_id,
            lga_id=parents[0].lga_id,
            land_use_type=parents[0].land_use_type,
            fallback_owner=parents[0].owner_stin,
            fallback_survey_plan=parents[0].survey_plan_no,
            title_type=parents[0].title_type,
            parents=parents,
        )
        self._check_area_conservation([child_area], sum(parent_areas))

        superseded = [
            p.model_copy(update={"status": ParcelStatus.SUPERSEDED}) for p in parents
        ]
        for record in superseded:
            self._repo.update(record)
        self._repo.add(child_record)
        self._log.record(
            "PARCEL_MERGED",
            tenant_state_id,
            parcel_id=child_record.parcel_id,
            actor=actor,
            detail={
                "child_parcel_uin": child_record.parcel_uin,
                "child_area_sqm": child_record.area_sqm,
                "parents": [
                    {"parcel_id": str(p.parcel_id), "parcel_uin": p.parcel_uin}
                    for p in parents
                ],
            },
        )
        return superseded, child_record
