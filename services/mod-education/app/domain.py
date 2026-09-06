"""Domain: tertiary consolidated billing, registration lock, Mojaloop clearing.

Amounts are integer kobo. Settlement legs post to the TigerBeetle ledger under
transfer code 150 (hospital/education consolidated billing); the institution
retention leg uses account class 2xxx, the state CRF leg 3001.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

LEDGER_TRANSFER_CODE = 150


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class InvoiceStatus(str, Enum):
    ISSUED = "issued"
    PAID = "paid"
    VOID = "void"


class Student(BaseModel):
    student_id: str
    tenant_state_id: str
    institution_id: str  # e.g. UNIOSUN, STATE-POLY
    matric_no: str
    enrolled_at: str


class FeeLine(BaseModel):
    fee_type: str  # TUITION | DEPARTMENTAL_LEVY | ACCOMMODATION | ...
    amount_kobo: int = Field(..., ge=0)


class StudentInvoice(BaseModel):
    invoice_id: str
    student_id: str
    session: str  # e.g. "2026/2027"
    lines: list[FeeLine]
    status: InvoiceStatus
    issued_at: str
    paid_at: str | None = None
    ledger_transfer_code: int = LEDGER_TRANSFER_CODE

    @property
    def total_kobo(self) -> int:
        return sum(l.amount_kobo for l in self.lines)


class Registration(BaseModel):
    registration_id: str
    student_id: str
    session: str
    courses: list[str]
    registered_at: str


class EducationStore:
    """Thread-safe in-memory store. Production: PostgreSQL (RLS) + Mojaloop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.students: dict[str, Student] = {}
        self.invoices: dict[str, StudentInvoice] = {}
        self.registrations: dict[str, Registration] = {}
        self.clearing_events: list[dict] = []  # Mojaloop webhook receipts

    def enroll(self, tenant_state_id: str, institution_id: str, matric_no: str) -> Student:
        stu = Student(student_id=_id("stu"), tenant_state_id=tenant_state_id,
                      institution_id=institution_id, matric_no=matric_no, enrolled_at=_now())
        with self._lock:
            self.students[stu.student_id] = stu
        return stu

    def issue_invoice(self, student_id: str, session: str, lines: list[FeeLine]) -> StudentInvoice:
        if student_id not in self.students:
            raise KeyError(f"student '{student_id}' not found")
        inv = StudentInvoice(invoice_id=_id("einv"), student_id=student_id, session=session,
                             lines=lines, status=InvoiceStatus.ISSUED, issued_at=_now())
        with self._lock:
            self.invoices[inv.invoice_id] = inv
        return inv

    def outstanding_kobo(self, student_id: str) -> int:
        """Sum of unpaid (issued) invoice totals — drives the registration lock."""
        return sum(inv.total_kobo for inv in self.invoices.values()
                   if inv.student_id == student_id and inv.status == InvoiceStatus.ISSUED)

    def mark_paid(self, invoice_id: str, via: str = "portal") -> StudentInvoice:
        with self._lock:
            inv = self.invoices.get(invoice_id)
            if inv is None:
                raise KeyError(f"invoice '{invoice_id}' not found")
            if inv.status != InvoiceStatus.ISSUED:
                raise ValueError(f"invoice {invoice_id} is {inv.status}")
            inv.status = InvoiceStatus.PAID
            inv.paid_at = _now()
            self.clearing_events.append({"invoice_id": invoice_id, "via": via, "at": _now()})
            return inv

    def register_courses(self, student_id: str, session: str, courses: list[str]) -> Registration:
        """Course registration is locked while the student has unpaid fees."""
        if student_id not in self.students:
            raise KeyError(f"student '{student_id}' not found")
        outstanding = self.outstanding_kobo(student_id)
        if outstanding > 0:
            raise PermissionError(
                f"course registration locked: ₦{outstanding / 100:,.2f} outstanding; "
                "clear fees via the billing portal to unlock"
            )
        reg = Registration(registration_id=_id("reg"), student_id=student_id,
                           session=session, courses=courses, registered_at=_now())
        with self._lock:
            self.registrations[reg.registration_id] = reg
        return reg

    def apply_mojaloop_transfer(self, transfer_id: str, invoice_id: str,
                                amount_kobo: int) -> StudentInvoice:
        """Mojaloop-clearing webhook: credit an invoice from a completed transfer."""
        inv = self.invoices.get(invoice_id)
        if inv is None:
            raise KeyError(f"invoice '{invoice_id}' not found")
        if amount_kobo < inv.total_kobo:
            raise ValueError(
                f"transfer {transfer_id} underpays invoice {invoice_id}: "
                f"{amount_kobo} < {inv.total_kobo} kobo"
            )
        return self.mark_paid(invoice_id, via=f"mojaloop:{transfer_id}")
