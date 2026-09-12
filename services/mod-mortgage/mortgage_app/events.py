"""AsyncAPI-style event payloads for mod-mortgage.

Published on the shared bus (``services/_shared/eventbus`` idiom — in-memory
default, Kafka in production), mirroring the event-model style of
mod-safecity-vision and mod-agri-trace.
"""

from __future__ import annotations

from pydantic import BaseModel

# --- topic constants ---------------------------------------------------------
EVENT_APPLICATION_RECEIVED = "ng.sos.mortgage.application_received"
EVENT_CREDIT_SCORED = "ng.sos.mortgage.credit_scored"
EVENT_APPROVED = "ng.sos.mortgage.approved"
EVENT_DECLINED = "ng.sos.mortgage.declined"
EVENT_LIEN_REGISTERED = "ng.sos.mortgage.lien_registered"
EVENT_DISBURSED = "ng.sos.mortgage.disbursed"
EVENT_PAYMENT_APPLIED = "ng.sos.mortgage.payment_applied"
EVENT_DISCHARGED = "ng.sos.mortgage.discharged"
EVENT_DEFAULTED = "ng.sos.mortgage.defaulted"
EVENT_FORECLOSED = "ng.sos.mortgage.foreclosed"


class MortgageEvent(BaseModel):
    """Common envelope: every mortgage event is tenant- and mortgage-scoped."""

    tenant_state_id: str
    mortgage_id: str
    occurred_at: str


class ApplicationReceivedEvent(MortgageEvent):
    applicant_id: str
    parcel_id: str
    principal_kobo: int


class CreditScoredEvent(MortgageEvent):
    applicant_id: str
    score: int
    outcome: str  # approved | manual_review | declined


class ApprovedEvent(MortgageEvent):
    officer: str = ""
    reason: str = ""


class DeclinedEvent(MortgageEvent):
    score: int


class LienRegisteredEvent(MortgageEvent):
    lien_id: str
    parcel_id: str
    title_ref: str
    priority: int  # 1 = senior, 2 = second charge


class DisbursedEvent(MortgageEvent):
    amount_kobo: int
    transfer_id: str


class PaymentAppliedEvent(MortgageEvent):
    payment_id: str
    amount_kobo: int
    interest_kobo: int
    principal_kobo: int
    outstanding_principal_kobo: int


class DischargedEvent(MortgageEvent):
    lien_id: str


class DefaultedEvent(MortgageEvent):
    days_past_due: int


class ForeclosedEvent(MortgageEvent):
    reason: str
