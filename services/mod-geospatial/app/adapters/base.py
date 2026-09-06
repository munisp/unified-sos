"""Adapter base contracts and environment helpers."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from ..domain import AdapterUnavailableError

#: Environment variable selecting the runtime mode. Anything other than
#: ``production`` is treated as local/test and uses deterministic adapters.
MODE_ENV = "GEOSPATIAL_MODE"


def current_mode() -> str:
    return os.environ.get(MODE_ENV, "local").strip().lower() or "local"


def is_production() -> bool:
    return current_mode() == "production"


def require_production_config(value: str | None, what: str) -> str:
    """Fail-closed guard for production adapter configuration."""

    if not value:
        raise AdapterUnavailableError(f"{what} is not configured; adapter fails closed")
    return value


@runtime_checkable
class H3Adapter(Protocol):
    def cell_for_point(self, lat: float, lon: float, resolution: int) -> str: ...

    def cells_for_geometry(self, geometry: dict, resolution: int) -> list[str]: ...

    @property
    def backend_name(self) -> str: ...


@runtime_checkable
class LakehouseAdapter(Protocol):
    def export_features(self, features: list[dict], destination: str, crs: str = "EPSG:4326") -> dict: ...


@runtime_checkable
class GeoLibreProjectAdapter(Protocol):
    def build_project(self, *, name: str, layers: list[dict], redaction_level: str) -> dict: ...


@runtime_checkable
class ProcessingAdapter(Protocol):
    def run_tool(self, tool: str, parameters: dict) -> dict: ...
