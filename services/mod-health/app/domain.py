"""Domain: hospital unified e-billing, SHIA/NHIS claims, pharmacy stock.

All monetary amounts are integer kobo. Movements post to the TigerBeetle
ledger under transfer code 150 (hospital/education consolidated billing) in
production; this reference records the posting intent on each transition.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

LEDGER_TRANSFER_CODE = 150  # hospital/education consolidated billing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class InvoiceStatus(str, Enum):
    ISSUED = "issued"
    PAID = "paid"
    VOID = "void"


class ClaimStatus(str, Enum):
    """SHIA/NHIS claim record lifecycle."""

    SUBMITTED = "submitted"
    ADJUDICATED = "adjudicated"
    PAID = "paid"
    REJECTED = "rejected"


class BillingAccount(BaseModel):
    account_id: str
    tenant_state_id: str
    facility_id: str
    patient_ref: str  # opaque data-plane reference (FHIR Patient/{id})
    opened_at: str


class InvoiceLine(BaseModel):
    service_code: str  # e.g. CONSULT, LAB_PANEL, DRUG:<drug_code>
    description: str = ""
    quantity: int = Field(default=1, ge=1)
    unit_amount_kobo: int = Field(..., ge=0)


class Invoice(BaseModel):
    invoice_id: str
    account_id: str
    tenant_state_id: str
    facility_id: str
    lines: list[InvoiceLine]
    status: InvoiceStatus
    issued_at: str
    paid_at: str | None = None
    ledger_transfer_code: int = LEDGER_TRANSFER_CODE

    @property
    def total_kobo(self) -> int:
        return sum(l.quantity * l.unit_amount_kobo for l in self.lines)


class Claim(BaseModel):
    claim_id: str
    invoice_id: str
    tenant_state_id: str
    payer: str  # SHIA | NHIS
    amount_kobo: int
    status: ClaimStatus
    submitted_at: str
    adjudicated_at: str | None = None
    adjudication_note: str | None = None


class StockOutError(Exception):
    """Raised when a dispense request exceeds available pharmacy stock."""

    def __init__(self, drug_code: str, requested: int, available: int) -> None:
        self.drug_code, self.requested, self.available = drug_code, requested, available
        super().__init__(f"stock-out: {drug_code} requested={requested} available={available}")


class HealthStore:
    """Thread-safe in-memory store. Production: PostgreSQL with RLS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.accounts: dict[str, BillingAccount] = {}
        self.invoices: dict[str, Invoice] = {}
        self.claims: dict[str, Claim] = {}
        self.drug_stock: dict[tuple[str, str], int] = {}  # (facility_id, drug_code) -> units

    # --- billing accounts -------------------------------------------------
    def open_account(self, tenant_state_id: str, facility_id: str, patient_ref: str) -> BillingAccount:
        acct = BillingAccount(account_id=_id("hba"), tenant_state_id=tenant_state_id,
                              facility_id=facility_id, patient_ref=patient_ref, opened_at=_now())
        with self._lock:
            self.accounts[acct.account_id] = acct
        return acct

    # --- invoices ----------------------------------------------------------
    def issue_invoice(self, account_id: str, lines: list[InvoiceLine]) -> Invoice:
        """Issue an invoice. Drug lines are stock-out-aware: the pharmacy hook
        reserves stock atomically with issuance, rejecting on stock-out."""
        acct = self.accounts.get(account_id)
        if acct is None:
            raise KeyError(f"billing account '{account_id}' not found")
        with self._lock:
            for line in lines:
                if line.service_code.startswith("DRUG:"):
                    drug_code = line.service_code.removeprefix("DRUG:")
                    key = (acct.facility_id, drug_code)
                    available = self.drug_stock.get(key, 0)
                    if available < line.quantity:
                        raise StockOutError(drug_code, line.quantity, available)
                    self.drug_stock[key] = available - line.quantity  # reserve
            inv = Invoice(invoice_id=_id("inv"), account_id=account_id,
                          tenant_state_id=acct.tenant_state_id, facility_id=acct.facility_id,
                          lines=lines, status=InvoiceStatus.ISSUED, issued_at=_now())
            self.invoices[inv.invoice_id] = inv
            return inv

    def pay_invoice(self, invoice_id: str) -> Invoice:
        with self._lock:
            inv = self.invoices.get(invoice_id)
            if inv is None:
                raise KeyError(f"invoice '{invoice_id}' not found")
            if inv.status != InvoiceStatus.ISSUED:
                raise ValueError(f"invoice {invoice_id} is {inv.status}; only issued invoices are payable")
            inv.status = InvoiceStatus.PAID
            inv.paid_at = _now()
            return inv

    # --- claims ------------------------------------------------------------
    def submit_claim(self, invoice_id: str, payer: str) -> Claim:
        with self._lock:
            inv = self.invoices.get(invoice_id)
            if inv is None:
                raise KeyError(f"invoice '{invoice_id}' not found")
            claim = Claim(claim_id=_id("clm"), invoice_id=invoice_id,
                          tenant_state_id=inv.tenant_state_id, payer=payer,
                          amount_kobo=inv.total_kobo, status=ClaimStatus.SUBMITTED,
                          submitted_at=_now())
            self.claims[claim.claim_id] = claim
            return claim

    def adjudicate_claim(self, claim_id: str, approve: bool, note: str = "") -> Claim:
        with self._lock:
            claim = self.claims.get(claim_id)
            if claim is None:
                raise KeyError(f"claim '{claim_id}' not found")
            if claim.status != ClaimStatus.SUBMITTED:
                raise ValueError(f"claim {claim_id} already {claim.status}")
            claim.status = ClaimStatus.ADJUDICATED if approve else ClaimStatus.REJECTED
            claim.adjudicated_at = _now()
            claim.adjudication_note = note
            return claim

    def settle_claim(self, claim_id: str) -> Claim:
        with self._lock:
            claim = self.claims.get(claim_id)
            if claim is None:
                raise KeyError(f"claim '{claim_id}' not found")
            if claim.status != ClaimStatus.ADJUDICATED:
                raise ValueError(f"claim {claim_id} is {claim.status}; only adjudicated claims settle")
            claim.status = ClaimStatus.PAID
            return claim

    # --- pharmacy stock ------------------------------------------------------
    def restock(self, facility_id: str, drug_code: str, quantity: int) -> dict[str, Any]:
        with self._lock:
            key = (facility_id, drug_code)
            self.drug_stock[key] = self.drug_stock.get(key, 0) + quantity
            return {"facility_id": facility_id, "drug_code": drug_code,
                    "available_units": self.drug_stock[key]}

    def stock_level(self, facility_id: str, drug_code: str) -> int:
        return self.drug_stock.get((facility_id, drug_code), 0)
