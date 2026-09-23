"""Performance-oriented tests: prepared geometries, geometry cache reuse,
and O(1) tenant-scoped UIN lookups. Semantics (EncumbranceGuard, overlap
inclusion of REGISTERED titles) are unchanged — see test_geometry.py."""

from __future__ import annotations

from uuid import uuid4

import pytest
from shapely.geometry import shape

from lands_app.geometry import DEFAULT_OVERLAP_TOLERANCE_M2, OverlapError, find_overlap
from lands_app.repository import InMemoryParcelRepository
from lands_app.schemas import LandUseType, ParcelRecord, ParcelStatus

from helpers import BASE_LAT, BASE_LON, geodesic_area_sqm, square_geojson


def _record(uin: str, geojson: dict, status: ParcelStatus = ParcelStatus.ACTIVE,
            tenant: str = "ogun") -> ParcelRecord:
    return ParcelRecord(
        parcel_id=uuid4(), tenant_state_id=tenant, lga_id="abeokuta-south",
        parcel_uin=uin, owner_stin="STIN-OG-0001234",
        land_use_type=LandUseType.RESIDENTIAL, survey_plan_no=f"OG/SVY/{uin}",
        beacon_count=4, area_sqm=round(geodesic_area_sqm(geojson), 2),
        status=status, boundary_geojson=geojson, titling_workflow_id="",
    )


def test_geometries_parsed_once_and_reused() -> None:
    """active_geometries must return the SAME shapely object across calls."""
    repo = InMemoryParcelRepository()
    repo.add(_record("UIN-001", square_geojson(BASE_LON, BASE_LAT)))
    first = repo.active_geometries("ogun")
    second = repo.active_geometries("ogun")
    assert first[0][1] is second[0][1]


def test_geom_cache_invalidated_on_new_boundary_object() -> None:
    repo = InMemoryParcelRepository()
    rec = _record("UIN-002", square_geojson(BASE_LON, BASE_LAT))
    repo.add(rec)
    geom_before = repo.active_geometries("ogun")[0][1]
    moved = rec.model_copy(update={"boundary_geojson":
                                   square_geojson(BASE_LON + 0.01, BASE_LAT)})
    repo.update(moved)
    geom_after = repo.active_geometries("ogun")[0][1]
    assert geom_after is not geom_before
    assert geom_after.equals(shape(moved.boundary_geojson))


def test_get_by_uin_uses_index() -> None:
    """UIN lookups are O(1) via the per-tenant index (no full scan)."""
    repo = InMemoryParcelRepository()
    for i in range(50):
        repo.add(_record(f"UIN-{i:03d}", square_geojson(BASE_LON + i * 0.002, BASE_LAT)))
    rec = repo.get_by_uin("ogun", "UIN-049")
    assert rec is not None and rec.parcel_uin == "UIN-049"
    assert repo._by_uin["ogun"]["UIN-049"] == rec.parcel_id
    # tenant isolation preserved
    assert repo.get_by_uin("lagos", "UIN-049") is None


def test_find_overlap_semantics_preserved_with_prepared_candidate() -> None:
    """Prepared-geometry fast path must keep trigger semantics exactly:
    interior overlap conflicts (incl. REGISTERED), shared edges do not."""
    base = square_geojson(BASE_LON, BASE_LAT)
    overlapping = square_geojson(BASE_LON + 0.0005, BASE_LAT)  # 50% shift
    touching = square_geojson(BASE_LON + 0.001, BASE_LAT)  # shares an edge
    existing = [("UIN-REG", shape(base))]
    candidate = shape(overlapping)
    conflict = find_overlap(candidate, existing)
    assert isinstance(conflict, OverlapError)
    assert conflict.conflicting_parcel_uin == "UIN-REG"
    assert conflict.overlap_area_m2 > DEFAULT_OVERLAP_TOLERANCE_M2
    # repeat call on the same (now prepared) candidate — same result
    again = find_overlap(candidate, existing)
    assert again is not None and again.conflicting_parcel_uin == "UIN-REG"
    assert find_overlap(shape(touching), existing) is None


@pytest.mark.parametrize("status", [ParcelStatus.ACTIVE, ParcelStatus.REGISTERED])
def test_registered_titles_still_block_overlap(status: ParcelStatus) -> None:
    repo = InMemoryParcelRepository()
    repo.add(_record("UIN-LIVE", square_geojson(BASE_LON, BASE_LAT), status=status))
    geoms = repo.active_geometries("ogun")
    assert len(geoms) == 1  # REGISTERED participates in overlap checks
    conflict = find_overlap(shape(square_geojson(BASE_LON + 0.0005, BASE_LAT)), geoms)
    assert conflict is not None
