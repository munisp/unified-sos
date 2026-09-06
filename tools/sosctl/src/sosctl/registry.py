"""Local tenant registry backing ``sosctl tenant list|status|suspend``.

The registry is a plain JSON file (default ``./out/registry/tenants.json``).
This is the local development backend; in production the source of truth is
the control-plane Tenant Operator API (services/control-plane,
``contracts/openapi/control-plane.yaml``) persisting to its metadata store.
The registry holds metadata only — never citizen PII or financial balances.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .states import STATE_TENANT_IDS, TIERS

DEFAULT_REGISTRY_PATH = Path("out/registry/tenants.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TenantRegistry:
    """File-backed tenant registry with atomic writes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or os.environ.get("SOSCTL_REGISTRY", DEFAULT_REGISTRY_PATH))

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "tenants": {}}
        return json.loads(self.path.read_text())

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file + rename, so concurrent readers never see partial JSON.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def create(self, state: str, tier: str, manifest: dict[str, str]) -> dict[str, Any]:
        """Register a tenant. Idempotent: re-creating an existing tenant is a no-op."""
        if state not in STATE_TENANT_IDS:
            raise ValueError(f"unknown state '{state}'")
        if tier not in TIERS:
            raise ValueError(f"unknown tier '{tier}'")
        data = self._load()
        tenants = data["tenants"]
        if state in tenants:
            return tenants[state]
        record = {
            "tenant_id": f"tn-{state}",
            "state": state,
            "tier": tier,
            "status": "active",
            "created_at": _now(),
            "suspended_at": None,
            "suspend_reason": None,
            "provisioned_resources": manifest,
            "backend_note": (
                "Local registry file. Production backend: control-plane Tenant "
                "Operator API (contracts/openapi/control-plane.yaml)."
            ),
        }
        tenants[state] = record
        self._save(data)
        return record

    def get(self, state: str) -> dict[str, Any] | None:
        return self._load()["tenants"].get(state)

    def list(self) -> list[dict[str, Any]]:
        tenants = self._load()["tenants"]
        return [tenants[k] for k in sorted(tenants)]

    def suspend(self, state: str, reason: str) -> dict[str, Any]:
        """Suspend a tenant. Idempotent: re-suspending updates the reason only."""
        data = self._load()
        record = data["tenants"].get(state)
        if record is None:
            raise KeyError(f"tenant '{state}' not found in registry")
        if record["status"] != "suspended":
            record["status"] = "suspended"
            record["suspended_at"] = _now()
        record["suspend_reason"] = reason
        self._save(data)
        return record
