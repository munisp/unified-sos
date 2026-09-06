"""Local verification runner for ``sedona/unassessed_property_join.sql``.

Re-executes the production classification CASE expression —

    WHEN p.parcel_uin IS NULL           THEN 'UNREGISTERED_ENCROACHMENT'
    WHEN p.assessed_annual_luc_kobo = 0 THEN 'UNASSESSED_IMPROVEMENT'
    ELSE 'COMPLIANT'

— over small GeoJSON fixtures with shapely, so CI verifies the join/classify
logic without a Spark/Sedona cluster. Production runs the SQL verbatim on
GeoParquet lakehouse tables; this runner accepts GeoJSON (always) and
GeoParquet (when geopandas+pyarrow are installed).

Note on FULL OUTER JOIN semantics: the production query filters
``WHERE b.tenant_state_id = '<state>'``, which discards parcel-only
(right-side-only) rows, so the effective output is one row per building
footprint of the tenant — this runner mirrors exactly that.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree


class AuditStatus(str, enum.Enum):
    UNREGISTERED_ENCROACHMENT = "UNREGISTERED_ENCROACHMENT"
    UNASSESSED_IMPROVEMENT = "UNASSESSED_IMPROVEMENT"
    COMPLIANT = "COMPLIANT"


@dataclass(frozen=True)
class FootprintFeature:
    building_footprint_id: str
    tenant_state_id: str
    estimated_area_sqm: float
    geom: BaseGeometry


@dataclass(frozen=True)
class ParcelFeature:
    parcel_uin: str
    tenant_state_id: str
    owner_stin: str
    assessed_annual_luc_kobo: int
    geom: BaseGeometry


@dataclass(frozen=True)
class JoinResultRow:
    """One output row — column-for-column match with the Sedona SQL SELECT."""

    building_footprint_id: str
    estimated_area_sqm: float
    parcel_uin: Optional[str]
    owner_stin: Optional[str]
    assessed_annual_luc_kobo: Optional[int]
    audit_status: AuditStatus


def _features_from_geojson(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        collection = json.load(fh)
    if collection.get("type") != "FeatureCollection":
        raise ValueError(f"{path} must be a GeoJSON FeatureCollection")
    return list(collection.get("features", []))


def load_buildings(path: str | Path) -> list[FootprintFeature]:
    """Load building footprints from GeoJSON (or GeoParquet if geopandas is present)."""
    if str(path).endswith(".parquet"):
        return _load_buildings_geoparquet(path)
    out = []
    for f in _features_from_geojson(path):
        props = f.get("properties", {})
        out.append(
            FootprintFeature(
                building_footprint_id=props["building_footprint_id"],
                tenant_state_id=props["tenant_state_id"],
                estimated_area_sqm=float(props["estimated_area_sqm"]),
                geom=shape(f["geometry"]),
            )
        )
    return out


def load_parcels(path: str | Path) -> list[ParcelFeature]:
    if str(path).endswith(".parquet"):
        return _load_parcels_geoparquet(path)
    out = []
    for f in _features_from_geojson(path):
        props = f.get("properties", {})
        out.append(
            ParcelFeature(
                parcel_uin=props["parcel_uin"],
                tenant_state_id=props["tenant_state_id"],
                owner_stin=props["owner_stin"],
                assessed_annual_luc_kobo=int(props["assessed_annual_luc_kobo"]),
                geom=shape(f["geometry"]),
            )
        )
    return out


def _load_buildings_geoparquet(path):  # pragma: no cover - optional dependency path
    import geopandas as gpd

    gdf = gpd.read_parquet(path)
    return [
        FootprintFeature(r.building_footprint_id, r.tenant_state_id, float(r.estimated_area_sqm), r.geometry)
        for r in gdf.itertuples()
    ]


def _load_parcels_geoparquet(path):  # pragma: no cover - optional dependency path
    import geopandas as gpd

    gdf = gpd.read_parquet(path)
    return [
        ParcelFeature(r.parcel_uin, r.tenant_state_id, r.owner_stin, int(r.assessed_annual_luc_kobo), r.geometry)
        for r in gdf.itertuples()
    ]


def classify_footprint(
    building: FootprintFeature, parcel: Optional[ParcelFeature]
) -> JoinResultRow:
    """Apply the production CASE expression to one (footprint, parcel?) pair."""

    if parcel is None:
        return JoinResultRow(
            building.building_footprint_id, building.estimated_area_sqm,
            None, None, None, AuditStatus.UNREGISTERED_ENCROACHMENT,
        )
    if parcel.assessed_annual_luc_kobo == 0:
        status = AuditStatus.UNASSESSED_IMPROVEMENT
    else:
        status = AuditStatus.COMPLIANT
    return JoinResultRow(
        building.building_footprint_id, building.estimated_area_sqm,
        parcel.parcel_uin, parcel.owner_stin, parcel.assessed_annual_luc_kobo, status,
    )


def run_unassessed_property_join(
    buildings: list[FootprintFeature],
    parcels: list[ParcelFeature],
    tenant_state_id: str,
) -> list[JoinResultRow]:
    """ST_Intersects spatial join + classification, tenant-scoped.

    Uses an STRtree over the tenant's parcels (the local analogue of Sedona's
    R-Tree spatial RDD partitioning).
    """

    tenant_buildings = [b for b in buildings if b.tenant_state_id == tenant_state_id]
    tenant_parcels = [p for p in parcels if p.tenant_state_id == tenant_state_id]

    tree = STRtree([p.geom for p in tenant_parcels]) if tenant_parcels else None
    rows: list[JoinResultRow] = []
    for building in tenant_buildings:
        match: Optional[ParcelFeature] = None
        if tree is not None:
            for idx in tree.query(building.geom, predicate="intersects"):
                match = tenant_parcels[int(idx)]
                break
        rows.append(classify_footprint(building, match))
    return rows
