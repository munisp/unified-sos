"""Domain model for mod-erp-bridge.

Tenant-isolated double-entry journal bridge: platform settlement events and
module postings are converted into balanced journal entries, mapped from the
state chart of accounts (COA) to ERP account codes, pushed to the configured
ERP backend, and recorded in a hash-chained outbound log.

Amounts are integer kobo (1/100 NGN) — never floats.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

TenantState = Literal["lagos", "ogun", "osun", "benue", "nasarawa", "taraba"]
TENANT_STATES = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class JournalLine(BaseModel):
    """One leg of a double-entry journal posting (kobo, XOR debit/credit)."""

    account_code: str = Field(min_length=1)
    debit_kobo: int = Field(default=0, ge=0)
    credit_kobo: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_sided(self) -> "JournalLine":
        if self.debit_kobo > 0 and self.credit_kobo > 0:
            raise ValueError("journal line cannot carry both debit and credit")
        if self.debit_kobo == 0 and self.credit_kobo == 0:
            raise ValueError("journal line must carry a debit or a credit")
        return self


class JournalEntry(BaseModel):
    """A balanced double-entry journal destined for the state ERP."""

    entry_id: str = Field(min_length=1)
    tenant_state_id: TenantState
    date: date
    memo: str = ""
    lines: List[JournalLine] = Field(min_length=2)
    source_event_id: str = Field(min_length=1)
    hash: Optional[str] = None

    @model_validator(mode="after")
    def _balanced(self) -> "JournalEntry":
        debits = sum(l.debit_kobo for l in self.lines)
        credits = sum(l.credit_kobo for l in self.lines)
        if debits <= 0 or credits <= 0:
            raise ValueError(
                f"journal entry {self.entry_id}: debits and credits must both be > 0"
            )
        if debits != credits:
            raise ValueError(
                f"journal entry {self.entry_id}: unbalanced (debits={debits} credits={credits})"
            )
        if self.hash is None:
            self.hash = self.compute_hash()
        return self

    def payload_dict(self) -> Dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "tenant_state_id": self.tenant_state_id,
            "date": self.date.isoformat(),
            "memo": self.memo,
            "lines": [
                {
                    "account_code": l.account_code,
                    "debit_kobo": l.debit_kobo,
                    "credit_kobo": l.credit_kobo,
                }
                for l in self.lines
            ],
            "source_event_id": self.source_event_id,
        }

    def compute_hash(self) -> str:
        return sha256_hex(canonical_json(self.payload_dict()))


class CoaMapping(BaseModel):
    """State chart-of-accounts code -> ERP account code mapping."""

    tenant_state_id: TenantState
    mapping: Dict[str, str] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utcnow)


class ErpBackend(str, Enum):
    FIXTURE = "fixture"
    ODOO = "odoo"
    ERPNEXT = "erpnext"
    IFMIS_EXPORT = "ifmis_export"


class ErpReceipt(BaseModel):
    """Acknowledgement returned by an ERP backend after posting."""

    receipt_id: str
    backend: ErpBackend
    external_ref: str
    entry_hash: str
    posted_at: datetime = Field(default_factory=utcnow)
    detail: str = ""


class PushStatus(str, Enum):
    PUSHED = "PUSHED"
    DEDUPED = "DEDUPED"
    RETRY_PENDING = "RETRY_PENDING"
    DEAD_LETTERED = "DEAD_LETTERED"


class OutboundRecord(BaseModel):
    """One hash-chained record of an outbound journal push."""

    event_id: str
    tenant_state_id: TenantState
    entry_id: str
    source_event_id: str
    entry_hash: str
    receipt: Optional[ErpReceipt] = None
    status: PushStatus
    prev_hash: str
    event_hash: str
    timestamp: datetime = Field(default_factory=utcnow)
