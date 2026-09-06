"""In-memory repositories for mod-environment (reference implementation).

All accessors are tenant-scoped: reads keyed by ID require the caller's
``tenant_state_id`` and raise ``NotFoundError`` on cross-tenant access.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .domain import (
    STATE_IDS,
    CarbonCredit,
    CarbonProject,
    ComplianceIncident,
    ComplianceLimit,
    DeforestationAlert,
    EIAApplication,
    Permit,
    TelemetryReading,
)


class NotFoundError(KeyError):
    pass


class InvalidTenantError(ValueError):
    pass


def validate_tenant(tenant_state_id: str) -> None:
    if tenant_state_id not in STATE_IDS:
        raise InvalidTenantError(f"unknown tenant_state_id {tenant_state_id!r}")


class _TenantRepo:
    """Base in-memory store keyed by entity ID with tenant isolation."""

    def __init__(self, id_attr: str) -> None:
        self._id_attr = id_attr
        self._items: Dict[str, object] = {}

    def add(self, item):
        validate_tenant(item.tenant_state_id)
        key = getattr(item, self._id_attr)
        self._items[key] = item
        return item

    def get(self, item_id: str, tenant_state_id: str):
        item = self._items.get(item_id)
        if item is None or item.tenant_state_id != tenant_state_id:
            raise NotFoundError(
                f"{self._id_attr} {item_id!r} not found for tenant {tenant_state_id!r}"
            )
        return item

    def list(self, tenant_state_id: Optional[str] = None) -> List:
        items = list(self._items.values())
        if tenant_state_id:
            items = [i for i in items if i.tenant_state_id == tenant_state_id]
        return items


class TelemetryRepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("reading_id")

    def list_for_facility(
        self, tenant_state_id: str, facility_id: str
    ) -> List[TelemetryReading]:
        return [
            r
            for r in self._items.values()
            if r.tenant_state_id == tenant_state_id and r.facility_id == facility_id
        ]


class IncidentRepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("incident_id")

    def list_for_facility(
        self, tenant_state_id: str, facility_id: str
    ) -> List[ComplianceIncident]:
        return [
            i
            for i in self._items.values()
            if i.tenant_state_id == tenant_state_id and i.facility_id == facility_id
        ]


class LimitRepository:
    """Config-like seed of per-state compliance limits (replaced by policy packs)."""

    def __init__(self, limits: List[ComplianceLimit]) -> None:
        self._limits = limits

    def find(
        self, tenant_state_id: str, medium, parameter
    ) -> Optional[ComplianceLimit]:
        for lim in self._limits:
            if (
                lim.tenant_state_id == tenant_state_id
                and lim.medium == medium
                and lim.parameter == parameter
            ):
                return lim
        return None


class PermitRepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("permit_id")


class DeforestationAlertRepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("alert_id")


class CarbonProjectRepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("project_id")


class CarbonCreditRepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("credit_id")

    def serial_exists(self, tenant_state_id: str, serial: str) -> bool:
        return any(
            c.tenant_state_id == tenant_state_id and c.serial == serial
            for c in self._items.values()
        )


class EIARepository(_TenantRepo):
    def __init__(self) -> None:
        super().__init__("application_id")
