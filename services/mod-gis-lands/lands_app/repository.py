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
        """(parcel_uin, geometry) of all live parcels for overlap checking.

        The overlap predicate includes ``ACTIVE`` and ``REGISTERED`` parcels
        (subdivision/merger children are REGISTERED and must not escape the
        check); ``SUPERSEDED``, ``REVOKED``, and ``ARCHIVED`` rows are excluded.
        Mirrors the trigger's tenant predicate plus the live-status filter.
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


#: Parcel statuses whose geometry participates in overlap checks: live titles
#: (ACTIVE) and derived subdivision/merger children (REGISTERED). SUPERSEDED,
#: REVOKED, and ARCHIVED parcels never block new registrations.
OVERLAP_CHECKED_STATUSES = (ParcelStatus.ACTIVE, ParcelStatus.REGISTERED)


class InMemoryParcelRepository:
    """Tenant-isolated in-memory repository for tests and local runs."""

    def __init__(self) -> None:
        # tenant_state_id -> parcel_id -> record  (O(1) tenant-scoped lookup)
        self._by_id: dict[str, dict[UUID, ParcelRecord]] = {}
        # tenant_state_id -> parcel_uin -> parcel_id  (O(1) UIN lookup/index)
        self._by_uin: dict[str, dict[str, UUID]] = {}
        # (tenant_state_id, parcel_id) -> (boundary_geojson dict, geometry).
        # Parsed shapely geometries are reused across overlap/search calls as
        # long as the record's boundary dict object is unchanged (strong ref
        # held, so id-reuse after GC cannot alias). PERF: shape() re-parsing
        # dominated register/search latency.
        self._geom_cache: dict[tuple[str, UUID], tuple[dict, BaseGeometry]] = {}

    # -- helpers -----------------------------------------------------------
    def _tenant(self, tenant_state_id: str) -> dict[UUID, ParcelRecord]:
        return self._by_id.setdefault(tenant_state_id, {})

    def _tenant_uin(self, tenant_state_id: str) -> dict[str, UUID]:
        return self._by_uin.setdefault(tenant_state_id, {})

    def _geom(self, record: ParcelRecord) -> BaseGeometry:
        key = (record.tenant_state_id, record.parcel_id)
        cached = self._geom_cache.get(key)
        if cached is not None and cached[0] is record.boundary_geojson:
            return cached[1]
        geom = shape(record.boundary_geojson)
        self._geom_cache[key] = (record.boundary_geojson, geom)
        return geom

    # -- ParcelRepository implementation ------------------------------------
    def add(self, record: ParcelRecord) -> None:
        tenant = self._tenant(record.tenant_state_id)
        uin_index = self._tenant_uin(record.tenant_state_id)
        if record.parcel_uin in uin_index:
            raise DuplicateParcelError(
                f"parcel_uin {record.parcel_uin!r} already registered"
            )
        tenant[record.parcel_id] = record
        uin_index[record.parcel_uin] = record.parcel_id

    def get(self, tenant_state_id: str, parcel_id: UUID) -> Optional[ParcelRecord]:
        return self._tenant(tenant_state_id).get(parcel_id)

    def get_by_uin(self, tenant_state_id: str, parcel_uin: str) -> Optional[ParcelRecord]:
        parcel_id = self._tenant_uin(tenant_state_id).get(parcel_uin)
        if parcel_id is None:
            return None
        return self._tenant(tenant_state_id).get(parcel_id)

    def active_geometries(self, tenant_state_id: str) -> list[tuple[str, BaseGeometry]]:
        return [
            (r.parcel_uin, self._geom(r))
            for r in self._tenant(tenant_state_id).values()
            if r.status in OVERLAP_CHECKED_STATUSES
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
        tenant = self._tenant(record.tenant_state_id)
        prior = tenant.get(record.parcel_id)
        uin_index = self._tenant_uin(record.tenant_state_id)
        if prior is not None and prior.parcel_uin != record.parcel_uin:
            uin_index.pop(prior.parcel_uin, None)
        uin_index[record.parcel_uin] = record.parcel_id
        tenant[record.parcel_id] = record


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
