"""GeoLibre WASM processing adapter seam.

Production can invoke ``geolibre_wasm.run_tool()`` when the
``geolibre-wasm`` package is installed (or ``GEOLIBRE_WASM`` points at a
runtime). Local/test mode uses deterministic pure-Python processing for a
small labeled toolset. When no WASM runtime is available in production mode
the adapter fails closed.
"""

from __future__ import annotations

import math
import os
from typing import Any

from ..domain import AdapterUnavailableError
from .base import is_production

#: Tools supported by the deterministic local fallback.
_LOCAL_TOOLS = {"buffer", "centroid", "area"}


class GeoLibreWasmAdapter:
    """Runs GeoLibre/Whitebox tools in-process through WASM when available."""

    def __init__(self) -> None:
        self._wasm = None
        try:
            import geolibre_wasm

            self._wasm = geolibre_wasm
        except ImportError:
            if is_production() or os.environ.get("GEOLIBRE_WASM"):
                raise AdapterUnavailableError(
                    "geolibre_wasm runtime unavailable; processing adapter fails closed"
                )

    @property
    def backend_name(self) -> str:
        return "geolibre-wasm" if self._wasm is not None else "local-python-fallback"

    def run_tool(self, tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if self._wasm is not None:  # pragma: no cover - optional dependency path
            return self._wasm.run_tool(tool, parameters)
        return self._run_local(tool, parameters)

    def _run_local(self, tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Deterministic pure-Python local processing (clearly labeled)."""

        if tool not in _LOCAL_TOOLS:
            raise AdapterUnavailableError(
                f"local fallback cannot run tool {tool!r}; install geolibre-wasm"
            )
        from shapely.geometry import shape

        geom = shape(parameters["geometry"])
        if tool == "buffer":
            result = geom.buffer(float(parameters.get("distance", 0.0)))
        elif tool == "centroid":
            result = geom.centroid
        else:  # area
            return {"backend": self.backend_name, "tool": tool, "area": float(geom.area)}
        return {"backend": self.backend_name, "tool": tool, "geometry": result.__geo_interface__}


def hectares(area_sq_deg: float, at_lat: float) -> float:
    """Rough sq-degree -> hectare conversion used only for local metrics."""

    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(at_lat))
    return abs(area_sq_deg) * m_per_deg_lat * m_per_deg_lon / 10_000.0
