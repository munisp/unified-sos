"""FastAPI surface for mod-health (WP-10 / EPIC-12)."""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from .domain import (
    BillingAccount,
    Claim,
    HealthStore,
    Invoice,
    InvoiceLine,
    StockOutError,
)


class OpenAccountRequest(BaseModel):
    tenant_state_id: str
    facility_id: str
    patient_ref: str = Field(..., description="Opaque FHIR Patient reference")


class IssueInvoiceRequest(BaseModel):
    account_id: str
    lines: list[InvoiceLine] = Field(..., min_length=1)


class SubmitClaimRequest(BaseModel):
    invoice_id: str
    payer: Literal["SHIA", "NHIS"]


class AdjudicateRequest(BaseModel):
    approve: bool
    note: str = ""


class RestockRequest(BaseModel):
    facility_id: str
    drug_code: str
    quantity: int = Field(..., ge=1)


def get_store(request: Request) -> HealthStore:
    return request.app.state.store


def create_app(store: HealthStore | None = None) -> FastAPI:
    app = FastAPI(title="SOS mod-health — Hospital Unified E-Billing", version="0.1.0")
    app.state.store = store or HealthStore()

    @app.post("/health/v1/billing-accounts", status_code=status.HTTP_201_CREATED,
              response_model=BillingAccount)
    def open_account(req: OpenAccountRequest, store: HealthStore = Depends(get_store)):
        return store.open_account(req.tenant_state_id, req.facility_id, req.patient_ref)

    @app.post("/health/v1/invoices", status_code=status.HTTP_201_CREATED, response_model=Invoice)
    def issue_invoice(req: IssueInvoiceRequest, store: HealthStore = Depends(get_store)):
        """Issue a service/fee invoice. DRUG:<code> lines are stock-out-aware."""
        try:
            return store.issue_invoice(req.account_id, req.lines)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except StockOutError as exc:
            # Pharmacy stock-out hook: the drug line cannot be billed.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
                "error": "stock_out", "drug_code": exc.drug_code,
                "requested": exc.requested, "available": exc.available,
                "message": str(exc),
            })

    @app.post("/health/v1/invoices/{invoice_id}/pay", response_model=Invoice)
    def pay_invoice(invoice_id: str, store: HealthStore = Depends(get_store)):
        try:
            return store.pay_invoice(invoice_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/health/v1/claims", status_code=status.HTTP_201_CREATED, response_model=Claim)
    def submit_claim(req: SubmitClaimRequest, store: HealthStore = Depends(get_store)):
        try:
            return store.submit_claim(req.invoice_id, req.payer)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    @app.post("/health/v1/claims/{claim_id}/adjudicate", response_model=Claim)
    def adjudicate_claim(claim_id: str, req: AdjudicateRequest,
                         store: HealthStore = Depends(get_store)):
        try:
            return store.adjudicate_claim(claim_id, req.approve, req.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/health/v1/claims/{claim_id}/settle", response_model=Claim)
    def settle_claim(claim_id: str, store: HealthStore = Depends(get_store)):
        try:
            return store.settle_claim(claim_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/health/v1/pharmacy/stock")
    def restock(req: RestockRequest, store: HealthStore = Depends(get_store)):
        return store.restock(req.facility_id, req.drug_code, req.quantity)

    @app.get("/health/v1/pharmacy/stock/{facility_id}/{drug_code}")
    def stock_level(facility_id: str, drug_code: str, store: HealthStore = Depends(get_store)):
        return {"facility_id": facility_id, "drug_code": drug_code,
                "available_units": store.stock_level(facility_id, drug_code)}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
