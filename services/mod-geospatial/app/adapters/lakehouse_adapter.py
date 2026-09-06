"""Lakehouse export adapter.

Write paths, in priority order:

* **Delta Lake** — selected when ``GEOSPATIAL_LAKEHOUSE_URI`` is set. Requires
  the optional ``deltalake`` + ``pyarrow`` packages; fails closed in any mode
  when they are unavailable (a configured URI must never silently downgrade).
* **GeoParquet** — when geopandas+pyarrow are installed, writes a real
  GeoParquet file with WKB geometry and CRS metadata.
* **JSON fallback** — deterministic local-only ``.json`` artifact, clearly
  marked ``local_fallback``. Production mode fails closed if neither the
  Delta nor the parquet stack is available.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..domain import AdapterUnavailableError
from ..geometry import canonical_json, sha256_hex

LAKEHOUSE_URI_ENV = "GEOSPATIAL_LAKEHOUSE_URI"


def _parquet_stack_available() -> bool:
    try:
        import geopandas  # noqa: F401
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


def _delta_stack_available() -> bool:
    try:
        import deltalake  # noqa: F401
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


class LakehouseAdapter:
    """Exports features to the lakehouse (Delta / GeoParquet when possible)."""

    def __init__(self, output_dir: str, lakehouse_uri: Optional[str] = None) -> None:
        self.output_dir = output_dir
        self.lakehouse_uri = lakehouse_uri or os.environ.get(LAKEHOUSE_URI_ENV)

    def export_features(
        self,
        features: list[dict[str, Any]],
        destination: str,
        crs: str = "EPSG:4326",
    ) -> dict[str, Any]:
        """Export GeoJSON features; returns a manifest dict describing the
        artifact (uri, format, feature_count, sha256, fallback flag)."""

        if self.lakehouse_uri:
            return self._export_delta(features, destination, crs)
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

    # -- production delta path ------------------------------------------------
    def _export_delta(self, features: list[dict[str, Any]], destination: str, crs: str) -> dict[str, Any]:
        if not _delta_stack_available():
            raise AdapterUnavailableError(
                f"{LAKEHOUSE_URI_ENV} is set but deltalake/pyarrow are not installed; "
                "Delta Lake export fails closed"
            )
        import pyarrow as pa
        from deltalake import write_deltalake
        from shapely.geometry import shape
        from shapely import to_wkb

        prop_keys = sorted({k for f in features for k in (f.get("properties") or {})})
        table = pa.table(
            {
                **{k: [(f.get("properties") or {}).get(k) for f in features] for k in prop_keys},
                "geometry": pa.array(
                    [to_wkb(shape(f["geometry"])) for f in features], type=pa.binary()
                ),
            }
        )
        # GeoParquet-compatible geometry column metadata (WKB encoding).
        table = table.replace_schema_metadata(
            {
                "geo": canonical_json(
                    {"columns": {"geometry": {"encoding": "WKB", "crs": crs}}, "primary_column": "geometry"}
                )
            }
        )
        uri = f"{self.lakehouse_uri.rstrip('/')}/{destination.replace('/', '_')}"
        write_deltalake(uri, table, mode="append")
        digest = sha256_hex(canonical_json({"destination": destination, "crs": crs, "feature_count": len(features)}))
        return {
            "uri": uri,
            "format": "delta",
            "local_fallback": False,
            "feature_count": len(features),
            "crs": crs,
            "sha256": digest,
        }

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
