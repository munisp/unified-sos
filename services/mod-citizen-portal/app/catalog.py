"""Service catalog providers for mod-citizen-portal (CIT-11).

Two deterministic sources:

- :class:`StaticCatalogProvider` — the compiled-in ``DEFAULT_CATALOG`` seed;
  the always-available default.
- :class:`ModuleCatalogProvider` — filters the seed catalog against the
  state's enabled-module list (``config/states/<state>/modules.yaml``);
  presence of a module name in that file means enabled.

Fail-closed rule (mirrors services/mod-kyc-kyb/app/adapters/base.py): any
error reading or parsing the state config falls back to the static catalog
rather than exposing an empty or partial catalog.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Protocol, Set

from .domain import ServiceCatalogEntry
from .service import DEFAULT_CATALOG

# Repo layout: services/mod-citizen-portal/app/catalog.py -> repo root is 4 up.
DEFAULT_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config" / "states"


class CatalogProvider(Protocol):
    def entries(self, state_id: str) -> List[ServiceCatalogEntry]: ...


def _entry_from_seed(state_id: str, seed: tuple) -> ServiceCatalogEntry:
    code, name, category, mda, base, expedited, module, endpoint_hint = seed
    return ServiceCatalogEntry(
        service_code=code,
        state_id=state_id,
        name=name,
        category=category,
        mda=mda,
        base_fee_kobo=base,
        expedited_fee_kobo=expedited,
        module=module,
        endpoint_hint=endpoint_hint,
    )


class StaticCatalogProvider:
    """Deterministic default: every seed entry, every state."""

    def entries(self, state_id: str) -> List[ServiceCatalogEntry]:
        return [_entry_from_seed(state_id, seed) for seed in DEFAULT_CATALOG]


class ModuleCatalogProvider:
    """Catalog filtered by the state's enabled modules.

    Reads ``<config_root>/<state_id>/modules.yaml`` and keeps only seed
    entries whose ``module`` is listed there. Fail-closed: a missing or
    unparseable config falls back to the static catalog.
    """

    def __init__(self, config_root: Optional[Path] = None) -> None:
        self._config_root = Path(config_root) if config_root else DEFAULT_CONFIG_ROOT
        self._fallback = StaticCatalogProvider()

    def _enabled_modules(self, state_id: str) -> Optional[Set[str]]:
        """Return the enabled module names, or None to trigger fallback."""
        path = self._config_root / state_id / "modules.yaml"
        try:
            import yaml  # optional dependency; absence -> fail-closed fallback

            with path.open("r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            modules = doc.get("modules") if isinstance(doc, dict) else None
            if not isinstance(modules, list):
                return None
            names = {m.get("name") for m in modules if isinstance(m, dict)}
            names.discard(None)
            if not names:
                return None
            return names  # type: ignore[return-value]
        except Exception:
            return None  # fail-closed

    def entries(self, state_id: str) -> List[ServiceCatalogEntry]:
        enabled = self._enabled_modules(state_id)
        if enabled is None:
            return self._fallback.entries(state_id)
        return [
            _entry_from_seed(state_id, seed)
            for seed in DEFAULT_CATALOG
            if seed[6] in enabled
        ]
