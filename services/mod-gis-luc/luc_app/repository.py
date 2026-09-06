"""Persistence layer for LUC bills and valuation runs.

Tenant-isolated throughout (CONTRIBUTING.md rule 4). The in-memory
implementation backs tests/CI; production maps to the ``luc.bills`` table in
db/migrations/0003_land_use_charge.sql with RLS on ``tenant_state_id``.
"""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from .models import LUCBill, ValuationRunSummary


class BillRepository(Protocol):
    def add_bill(self, bill: LUCBill) -> None: ...
    def add_run(self, run: ValuationRunSummary) -> None: ...
    def get_bill(self, tenant_state_id: str, bill_id: UUID) -> Optional[LUCBill]: ...
    def list_bills(
        self, tenant_state_id: str, parcel_uin: Optional[str] = None
    ) -> list[LUCBill]: ...
    def list_runs(self, tenant_state_id: str) -> list[ValuationRunSummary]: ...


class InMemoryBillRepository:
    """Tenant-isolated in-memory repository for tests and local runs."""

    def __init__(self) -> None:
        self._bills: dict[str, dict[UUID, LUCBill]] = {}
        self._runs: dict[str, list[ValuationRunSummary]] = {}

    def add_bill(self, bill: LUCBill) -> None:
        self._bills.setdefault(bill.tenant_state_id, {})[bill.bill_id] = bill

    def add_run(self, run: ValuationRunSummary) -> None:
        self._runs.setdefault(run.tenant_state_id, []).append(run)

    def get_bill(self, tenant_state_id: str, bill_id: UUID) -> Optional[LUCBill]:
        return self._bills.get(tenant_state_id, {}).get(bill_id)

    def list_bills(
        self, tenant_state_id: str, parcel_uin: Optional[str] = None
    ) -> list[LUCBill]:
        bills = list(self._bills.get(tenant_state_id, {}).values())
        if parcel_uin is not None:
            bills = [b for b in bills if b.parcel_uin == parcel_uin]
        return bills

    def list_runs(self, tenant_state_id: str) -> list[ValuationRunSummary]:
        return list(self._runs.get(tenant_state_id, []))
