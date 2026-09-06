"""Persistence layer for cadastre.parcels.

``ParcelRepository`` is the seam between the service and storage. Production
deployments use :class:`PostGISParcelRepository` against the schema + RLS
policies in db/migrations/0001_cadastre.sql; tests and local development use
:class:`InMemoryParcelRepository`, which enforces the same tenancy isolation
(every query is scoped by ``tenant_state_id``, exactly like the
``parcel_state_isolation_policy`` row-level-security policy).
"""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely import wkt as shapely_wkt

from .geometry import GeometryError
from .schemas import ParcelRecord, ParcelStatus


class ParcelRepository(Protocol):
    """Storage contract for cadastral parcels (tenant-scoped throughout)."""

    def add(self, record: ParcelRecord) -> None:
        """Insert a new parcel row for its tenant."""
        ...

    def get(self, tenant_state_id: str, parcel_id: UUID) -> Optional[ParcelRecord]:
        """Fetch one parcel by primary key, tenant-scoped (RLS equivalent)."""
        ...

    def get_by_uin(self, tenant_state_id: str, parcel_uin: str) -> Optional[ParcelRecord]:
        """Fetch one parcel by its Unique Identification Number, tenant-scoped."""
        ...

    def active_geometries(self, tenant_state_id: str) -> list[tuple[str, BaseGeometry]]:
        """(parcel_uin, geometry) of all ACTIVE parcels for overlap checking.

        Mirrors the trigger's ``WHERE p.tenant_state_id = NEW.tenant_state_id
        AND p.status = 'ACTIVE'`` predicate.
        """
        ...

    def search(
        self,
        tenant_state_id: str,
        lga_id: Optional[str] = None,
        within: Optional[BaseGeometry] = None,
    ) -> list[ParcelRecord]:
        """Attribute + spatial search (GiST ``ST_Intersects`` in production)."""
        ...

    def update(self, record: ParcelRecord) -> None:
        """Persist title_type / c_of_o_number / status changes."""
        ...


class DuplicateParcelError(Exception):
    """parcel_uin UNIQUE constraint violation (cadastre.parcels.parcel_uin)."""


class InMemoryParcelRepository:
    """Tenant-isolated in-memory repository for tests and local runs."""

    def __init__(self) -> None:
        # tenant_state_id -> parcel_id -> record
        self._by_id: dict[str, dict[UUID, ParcelRecord]] = {}

    # -- helpers -----------------------------------------------------------
    def _tenant(self, tenant_state_id: str) -> dict[UUID, ParcelRecord]:
        return self._by_id.setdefault(tenant_state_id, {})

    @staticmethod
    def _geom(record: ParcelRecord) -> BaseGeometry:
        return shape(record.boundary_geojson)

    # -- ParcelRepository implementation ------------------------------------
    def add(self, record: ParcelRecord) -> None:
        tenant = self._tenant(record.tenant_state_id)
        if any(r.parcel_uin == record.parcel_uin for r in tenant.values()):
            raise DuplicateParcelError(
                f"parcel_uin {record.parcel_uin!r} already registered"
            )
        tenant[record.parcel_id] = record

    def get(self, tenant_state_id: str, parcel_id: UUID) -> Optional[ParcelRecord]:
        return self._tenant(tenant_state_id).get(parcel_id)

    def get_by_uin(self, tenant_state_id: str, parcel_uin: str) -> Optional[ParcelRecord]:
        for record in self._tenant(tenant_state_id).values():
            if record.parcel_uin == parcel_uin:
                return record
        return None

    def active_geometries(self, tenant_state_id: str) -> list[tuple[str, BaseGeometry]]:
        return [
            (r.parcel_uin, self._geom(r))
            for r in self._tenant(tenant_state_id).values()
            if r.status == ParcelStatus.ACTIVE
        ]

    def search(
        self,
        tenant_state_id: str,
        lga_id: Optional[str] = None,
        within: Optional[BaseGeometry] = None,
    ) -> list[ParcelRecord]:
        results = list(self._tenant(tenant_state_id).values())
        if lga_id is not None:
            results = [r for r in results if r.lga_id == lga_id]
        if within is not None:
            results = [r for r in results if self._geom(r).intersects(within)]
        return results

    def update(self, record: ParcelRecord) -> None:
        self._tenant(record.tenant_state_id)[record.parcel_id] = record


def parse_within_wkt(wkt_text: str) -> BaseGeometry:
    """Parse the ``within`` query parameter (WKT polygon, EPSG:4326)."""

    try:
        geom = shapely_wkt.loads(wkt_text)
    except Exception as exc:
        raise GeometryError(f"invalid WKT in 'within' parameter: {exc}") from exc
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise GeometryError("'within' parameter must be a WKT Polygon/MultiPolygon")
    return geom


class PostGISParcelRepository:
    """Production adapter — PostgreSQL 16 + PostGIS 3.4 (NOT exercised in tests).

    Wiring notes for deployment:

    * Connect via asyncpg/psycopg with the session variable
      ``SET app.current_state_tenant = :state_id`` so the RLS policy
      ``parcel_state_isolation_policy`` enforces tenancy in-database (0001_cadastre.sql).
    * ``add``            -> INSERT with ``ST_GeomFromGeoJSON(:boundary)``; the
      ``trg_parcels_no_overlap`` trigger rejects overlaps server-side; the
      service-level shapely check is a pre-validation for better UX/errors.
    * ``search``         -> ``WHERE tenant_state_id = :t [AND lga_id = :lga]
      [AND ST_Intersects(boundary_geom, ST_GeomFromText(:within, 4326))]``,
      served by ``idx_parcels_spatial`` (GiST).
    * ``update``         -> UPDATE of title_type / c_of_o_number / status.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _unavailable(self) -> None:
        raise NotImplementedError(
            "PostGISParcelRepository requires a live PostgreSQL/PostGIS DSN and "
            "driver wiring (asyncpg); see class docstring for the SQL mapping."
        )

    def add(self, record: ParcelRecord) -> None:  # pragma: no cover - stub
        self._unavailable()

    def get(self, tenant_state_id: str, parcel_id: UUID):  # pragma: no cover
        self._unavailable()

    def get_by_uin(self, tenant_state_id: str, parcel_uin: str):  # pragma: no cover
        self._unavailable()

    def active_geometries(self, tenant_state_id: str):  # pragma: no cover
        self._unavailable()

    def search(self, tenant_state_id: str, lga_id=None, within=None):  # pragma: no cover
        self._unavailable()

    def update(self, record: ParcelRecord) -> None:  # pragma: no cover
        self._unavailable()
