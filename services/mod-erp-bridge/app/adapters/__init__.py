"""mod-erp-bridge adapter registry (fail-closed selection idiom)."""
from __future__ import annotations

import os
from typing import Dict, Optional

from .base import AdapterUnavailableError, ErpAdapter, ErpPushError
from .erpnext_adapter import ErpNextAdapter
from .fixtures import FixtureErpAdapter
from .ifmis_export import IfmisExportAdapter
from .odoo_adapter import OdooAdapter

__all__ = [
    "AdapterUnavailableError",
    "ErpAdapter",
    "ErpPushError",
    "ErpNextAdapter",
    "FixtureErpAdapter",
    "IfmisExportAdapter",
    "OdooAdapter",
    "build_adapter",
]


def build_adapter(env: Optional[Dict[str, str]] = None) -> ErpAdapter:
    """Select the ERP adapter from ``ERP_BACKEND``.

    Default ``local``/unset is the deterministic fixture adapter. Production
    backends fail closed at boot when their configuration is incomplete.
    """
    env = dict(os.environ if env is None else env)
    backend = env.get("ERP_BACKEND", "local")
    if backend in ("local", "fixture"):
        return FixtureErpAdapter()
    if backend == "ifmis_export":
        return IfmisExportAdapter(out_dir=env.get("IFMIS_EXPORT_DIR"))
    if backend == "odoo":
        return OdooAdapter.from_env(env)
    if backend == "erpnext":
        return ErpNextAdapter.from_env(env)
    raise AdapterUnavailableError(
        f"unknown ERP_BACKEND {backend!r}; expected local|ifmis_export|odoo|erpnext"
    )
