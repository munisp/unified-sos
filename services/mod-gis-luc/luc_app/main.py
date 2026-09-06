"""mod-gis-luc FastAPI application — LUC valuation runs and bill retrieval.

Endpoints (tenant-scoped by state_id, mirroring the cadastre contract's
tenancy style):

* ``POST /api/v1/states/{state_id}/luc/valuation-runs`` — trigger a valuation
  run: bills the registry snapshot and ingests classified join findings.
* ``GET  /api/v1/states/{state_id}/luc/valuation-runs`` — list run summaries.
* ``GET  /api/v1/states/{state_id}/luc/bills[?parcel_uin=]`` — fetch bills.
* ``GET  /api/v1/states/{state_id}/luc/bills/{bill_id}`` — fetch one bill.

In production the same ingestion path is also driven by the Kafka consumer for
``ng.sos.gis.unassessed_property_discovered`` (see app/ingestion.py docstring);
the HTTP trigger is the operator/CI entry point.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from .ingestion import run_valuation
from .models import (
    JoinFinding,
    LUCBill,
    ParcelSnapshot,
    ValuationRunSummary,
)
from .repository import BillRepository, InMemoryBillRepository
from .tariffs import tariff_for_state

# State tenants this module deploys to (docs/architecture/02-bounded-contexts.md).
DEPLOYED_STATES = ("ogun", "benue", "lagos", "nasarawa")


class ValuationRunRequest(BaseModel):
    """Trigger body: registry snapshot + classified join findings."""

    assessment_year: int = Field(ge=2020, le=2100)
    parcels: list[ParcelSnapshot] = Field(default_factory=list)
    findings: list[JoinFinding] = Field(default_factory=list)


# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None


def create_app(repository: BillRepository | None = None) -> FastAPI:
    """Application factory — inject the bill repository for tests."""

    app = FastAPI(title="SOS Land Use Charge Valuation API", version="1.0.0")
    repo: BillRepository = repository or InMemoryBillRepository()

    def get_repo() -> BillRepository:
        return repo

    def _tariff(state_id: str):
        if state_id not in DEPLOYED_STATES:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"mod-gis-luc not deployed for state {state_id!r} "
                f"(deployed: {', '.join(DEPLOYED_STATES)})",
            )
        try:
            return tariff_for_state(state_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.post(
        "/api/v1/states/{state_id}/luc/valuation-runs",
        status_code=status.HTTP_201_CREATED,
        response_model=ValuationRunSummary,
    )
    def trigger_valuation_run(
        state_id: str, body: ValuationRunRequest, repo: BillRepository = Depends(get_repo)
    ) -> ValuationRunSummary:
        tariff = _tariff(state_id)
        summary, bills = run_valuation(
            tariff,
            parcels=body.parcels,
            findings=body.findings,
            assessment_year=body.assessment_year,
        )
        repo.add_run(summary)
        for bill in bills:
            repo.add_bill(bill)
        return summary

    @app.get(
        "/api/v1/states/{state_id}/luc/valuation-runs",
        response_model=list[ValuationRunSummary],
    )
    def list_valuation_runs(
        state_id: str, repo: BillRepository = Depends(get_repo)
    ) -> list[ValuationRunSummary]:
        _tariff(state_id)
        return repo.list_runs(state_id)

    @app.get("/api/v1/states/{state_id}/luc/bills", response_model=list[LUCBill])
    def list_bills(
        state_id: str,
        parcel_uin: Optional[str] = Query(default=None),
        repo: BillRepository = Depends(get_repo),
    ) -> list[LUCBill]:
        _tariff(state_id)
        return repo.list_bills(state_id, parcel_uin)

    @app.get("/api/v1/states/{state_id}/luc/bills/{bill_id}", response_model=LUCBill)
    def get_bill(
        state_id: str, bill_id: UUID, repo: BillRepository = Depends(get_repo)
    ) -> LUCBill:
        _tariff(state_id)
        bill = repo.get_bill(state_id, bill_id)
        if bill is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "bill not found")
        return bill

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-gis-luc")
    return app


#: Module-level app for ``uvicorn luc_app.main:app`` local runs.
app = create_app()
