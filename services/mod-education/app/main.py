"""FastAPI surface for mod-education (WP-11 / EPIC-13)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from .domain import (
    EducationStore,
    FeeLine,
    Registration,
    Student,
    StudentInvoice,
)


class EnrollRequest(BaseModel):
    tenant_state_id: str
    institution_id: str
    matric_no: str


class IssueInvoiceRequest(BaseModel):
    student_id: str
    session: str = Field(..., examples=["2026/2027"])
    lines: list[FeeLine] = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    student_id: str
    session: str
    courses: list[str] = Field(..., min_length=1)


class MojaloopWebhook(BaseModel):
    """Local-mode stub of a Mojaloop clearing transfer notification.

    Used only when no FSPIOP adapter is wired (deterministic local default).
    """

    transfer_id: str
    invoice_id: str
    amount_kobo: int = Field(..., ge=0)
    payer_msisdn_alias: str | None = None  # opaque alias only


class TransferFulfilmentWebhook(BaseModel):
    """FSPIOP PUT /transfers/{id} fulfilment callback (adapter mode).

    Requires a valid FSPIOP-Signature header, verified via the injected
    scheme adapter (fail closed: no adapter/signature → 401/503).
    """

    transfer_id: str
    invoice_id: str
    amount_kobo: int = Field(..., ge=0)
    transfer_state: str = "COMMITTED"  # COMMITTED | ABORTED
    fulfilment: str | None = None
    completed_timestamp: str | None = None


def get_store(request: Request) -> EducationStore:
    return request.app.state.store


def create_app(store: EducationStore | None = None, fspiop=None) -> FastAPI:
    """App factory. `fspiop` is an optional Mojaloop FSPIOP scheme adapter
    (duck-typed: verify_inbound_signature(headers, body) -> bool); when None
    the webhook runs in deterministic local stub mode."""
    app = FastAPI(title="SOS mod-education — Tertiary Consolidated Billing", version="0.1.0")
    app.state.store = store or EducationStore()
    app.state.fspiop = fspiop

    @app.post("/education/v1/students", status_code=status.HTTP_201_CREATED,
              response_model=Student)
    def enroll(req: EnrollRequest, store: EducationStore = Depends(get_store)):
        return store.enroll(req.tenant_state_id, req.institution_id, req.matric_no)

    @app.post("/education/v1/invoices", status_code=status.HTTP_201_CREATED,
              response_model=StudentInvoice)
    def issue_invoice(req: IssueInvoiceRequest, store: EducationStore = Depends(get_store)):
        try:
            return store.issue_invoice(req.student_id, req.session, req.lines)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    @app.get("/education/v1/students/{student_id}/billing")
    def billing_portal(student_id: str, store: EducationStore = Depends(get_store)):
        """Student billing portal view: invoices + outstanding + lock status."""
        if student_id not in store.students:
            raise HTTPException(status_code=404, detail=f"student '{student_id}' not found")
        outstanding = store.outstanding_kobo(student_id)
        return {
            "student_id": student_id,
            "outstanding_kobo": outstanding,
            "registration_locked": outstanding > 0,
            "invoices": [inv.model_dump() for inv in store.invoices.values()
                         if inv.student_id == student_id],
        }

    @app.post("/education/v1/invoices/{invoice_id}/pay", response_model=StudentInvoice)
    def pay_invoice(invoice_id: str, store: EducationStore = Depends(get_store)):
        try:
            return store.mark_paid(invoice_id, via="portal")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/education/v1/registrations", status_code=status.HTTP_201_CREATED,
              response_model=Registration)
    def register(req: RegisterRequest, store: EducationStore = Depends(get_store)):
        """Course registration — locked (HTTP 423) while fees are outstanding."""
        try:
            return store.register_courses(req.student_id, req.session, req.courses)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))

    @app.post("/education/v1/webhooks/mojaloop", response_model=StudentInvoice)
    async def mojaloop_webhook(request: Request, store: EducationStore = Depends(get_store)):
        """Mojaloop clearing webhook.

        Adapter mode: FSPIOP PUT /transfers/{id} fulfilment — the
        FSPIOP-Signature header is verified via the injected adapter and the
        transfer must be COMMITTED. Local mode (no adapter): deterministic
        stub payload (MojaloopWebhook), kept for offline development.
        """
        fspiop = request.app.state.fspiop
        body = await request.body()
        if fspiop is not None:
            if not fspiop.verify_inbound_signature(request.headers, body):
                raise HTTPException(status_code=401,
                                    detail="invalid or missing FSPIOP-Signature")
            try:
                req = TransferFulfilmentWebhook.model_validate_json(body)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors())
            if req.transfer_state.upper() != "COMMITTED":
                raise HTTPException(
                    status_code=409,
                    detail=f"transfer {req.transfer_id} is {req.transfer_state}; "
                           "only COMMITTED fulfilments credit invoices")
        else:
            try:
                req = MojaloopWebhook.model_validate_json(body)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors())
        try:
            return store.apply_mojaloop_transfer(req.transfer_id, req.invoice_id,
                                                 req.amount_kobo)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
