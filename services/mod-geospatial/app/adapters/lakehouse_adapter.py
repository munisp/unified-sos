"""Lakehouse / GeoParquet export adapter.

When geopandas+pyarrow are installed, ``export_features`` writes a real
GeoParquet file with WKB geometry and CRS metadata. Otherwise (and always in
local/test CI) it writes a deterministic JSON fallback with a ``.json``
suffix, clearly marked ``local_fallback``. Production mode fails closed if
the parquet stack is unavailable.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..domain import AdapterUnavailableError
from ..geometry import canonical_json, sha256_hex


def _parquet_stack_available() -> bool:
    try:
        import geopandas  # noqa: F401
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


class LakehouseAdapter:
    """Exports features to the lakehouse (GeoParquet when possible)."""

    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir

    def export_features(
        self,
        features: list[dict[str, Any]],
        destination: str,
        crs: str = "EPSG:4326",
    ) -> dict[str, Any]:
        """Export GeoJSON features; returns a manifest dict describing the
        artifact (uri, format, feature_count, sha256, fallback flag)."""

        os.makedirs(self.output_dir, exist_ok=True)
        safe_name = destination.replace("/", "_")
        if _parquet_stack_available():
            return self._export_parquet(features, safe_name, crs)
        from .base import is_production

        if is_production():
            raise AdapterUnavailableError(
                "geopandas/pyarrow unavailable in production mode; GeoParquet export fails closed"
            )
        return self._export_json_fallback(features, safe_name, crs)

    # -- production path ----------------------------------------------------
    def _export_parquet(self, features: list[dict[str, Any]], name: str, crs: str) -> dict[str, Any]:  # pragma: no cover
        import geopandas as gpd
        from shapely.geometry import shape

        gdf = gpd.GeoDataFrame(
            [f.get("properties", {}) for f in features],
            geometry=[shape(f["geometry"]) for f in features],
            crs=crs,
        )
        path = os.path.join(self.output_dir, f"{name}.parquet")
        gdf.to_parquet(path, index=False)  # writes WKB geometry + CRS metadata (GeoParquet spec)
        with open(path, "rb") as fh:
            digest = sha256_hex(fh.read())
        return {
            "uri": path,
            "format": "geoparquet",
            "local_fallback": False,
            "feature_count": len(features),
            "crs": crs,
            "sha256": digest,
        }

    # -- deterministic local fallback ---------------------------------------
    def _export_json_fallback(self, features: list[dict[str, Any]], name: str, crs: str) -> dict[str, Any]:
        path = os.path.join(self.output_dir, f"{name}.json")
        payload = {
            "format": "geojson-local-fallback",
            "note": "LOCAL FALLBACK — geopandas/pyarrow not installed; not a GeoParquet artifact",
            "crs": crs,
            "type": "FeatureCollection",
            "features": features,
        }
        blob = canonical_json(payload)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(blob)
        return {
            "uri": path,
            "format": "geojson-local-fallback",
            "local_fallback": True,
            "feature_count": len(features),
            "crs": crs,
            "sha256": sha256_hex(blob),
        }
