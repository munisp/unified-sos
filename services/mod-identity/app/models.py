"""Domain models for mod-identity (SOS module f — Identity & Data).

Flagship deployment: Lagos LASRRA resident registry (6.8M records [LIVE],
docs/states/lagos.md L2) operated as a governed verification-API business
(QoreID API-monetization precedent [LIVE]). The NDPA posture is enforced in
code, not policy prose:

- **Data minimization** — ``VerificationResult`` carries only a boolean /
  attestation reference, never the resident record.
- **Consent before verification** — ``ConsentGrant`` is purpose-scoped,
  expiring, revocable; the service layer refuses calls without an active grant.
- **Append-only audit** — every access (allowed or denied) lands in a
  hash-chained ``AuditEntry``.

Monetary amounts are integer kobo. Settlement lines reference the ledger
chart of accounts (``ledger/chart-of-accounts.md``): state share credits the
State Consolidated Revenue Fund (account 3001), the platform/concessionaire
share credits PPP Tech Concessionaire Escrow (account 2099, transfer code
103 — Concessionaire Share leg).
"""
from __future__ import annotations

import enum
import hashlib
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATE_IDS = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")


class VerificationProduct(str, enum.Enum):
    """Metered per-call verification API products (LASRRA/QoreID model [LIVE])."""

    ADDRESS_VERIFICATION = "ADDRESS_VERIFICATION"
    RESIDENCY_ATTESTATION = "RESIDENCY_ATTESTATION"
    KYC_ADJUNCT = "KYC_ADJUNCT"


class Resident(BaseModel):
    """State-scoped, NIN-linked resident record. Never leaves the module."""

    resident_id: str
    state_id: str = Field(description="tenant: lagos | ogun | osun | benue | nasarawa | taraba")
    nin: str = Field(description="National Identification Number linkage [LIVE: LASRRA records are NIN-linked]")
    full_name: str
    address: str
    registered_at: datetime = Field(default_factory=utcnow)
    active: bool = True


class Credential(BaseModel):
    """A verifiable credential issued against a resident record."""

    credential_id: str
    state_id: str
    resident_id: str
    credential_type: str = Field(description="e.g. RESIDENCY_CARD, LASRRA_ID")
    issued_at: datetime = Field(default_factory=utcnow)
    revoked_at: Optional[datetime] = None


class ApiConsumer(BaseModel):
    """Registered API consumer (bank, telco, fintech) buying metered calls."""

    consumer_id: str
    state_id: str
    name: str
    active: bool = True
    registered_at: datetime = Field(default_factory=utcnow)


class ConsentGrant(BaseModel):
    """NDPA-compliant consent: purpose-scoped, expiring, revocable.

    Revocation blocks all future verification calls for the
    (resident, consumer, purpose) tuple; past usage records remain in the
    append-only audit trail.
    """

    grant_id: str
    state_id: str
    resident_id: str
    consumer_id: str
    purpose: VerificationProduct
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked_at: Optional[datetime] = None

    def is_active(self, now: Optional[datetime] = None) -> bool:
        now = now or utcnow()
        return self.revoked_at is None and now < self.expires_at


class VerificationResult(BaseModel):
    """NDPA data-minimization envelope: boolean attestation ONLY.

    Structural guarantee — this model has no field capable of carrying the
    resident record (name, NIN, address) back to the caller.
    """

    result_id: str
    state_id: str
    consumer_id: str
    product: VerificationProduct
    attested: bool = Field(description="boolean outcome of the verification")
    consent_grant_id: str
    responded_at: datetime = Field(default_factory=utcnow)


class UsageRecord(BaseModel):
    """Metering record for one billable verification call (integer kobo)."""

    usage_id: str
    state_id: str
    consumer_id: str
    product: VerificationProduct
    fee_kobo: int = Field(ge=0)
    result_id: str
    recorded_at: datetime = Field(default_factory=utcnow)
    settlement_id: Optional[str] = None


class SettlementLine(BaseModel):
    """One revenue-share leg, wired to TigerBeetle (ADR-002 double-entry).

    ``tigerbeetle_account_code`` per ledger/chart-of-accounts.md:
    3001 State Consolidated Revenue Fund (state share), 2099 PPP Tech
    Concessionaire Escrow (platform share, transfer code 103).
    """

    beneficiary: str
    tigerbeetle_account_code: int
    transfer_code: int
    amount_kobo: int = Field(ge=0)


class SettlementRecord(BaseModel):
    """Periodic revenue-share settlement for a consumer's metered usage."""

    settlement_id: str
    state_id: str
    consumer_id: str
    usage_ids: list[str]
    total_kobo: int = Field(ge=0)
    lines: list[SettlementLine]
    settled_at: datetime = Field(default_factory=utcnow)


class AuditEntry(BaseModel):
    """Append-only, hash-chained audit record.

    ``entry_hash`` = sha256(prev_hash + canonical payload). The chain makes
    retroactive tampering detectable; the repository exposes no update or
    delete path.
    """

    seq: int
    action: str = Field(description="e.g. VERIFY_GRANTED, VERIFY_DENIED_NO_CONSENT")
    state_id: str
    actor_id: str = Field(description="consumer or operator principal")
    subject_id: str = Field(description="resident or record affected")
    details: str = ""
    prev_hash: str
    entry_hash: str
    at: datetime = Field(default_factory=utcnow)

    @staticmethod
    def compute_hash(prev_hash: str, payload: str) -> str:
        return hashlib.sha256((prev_hash + "|" + payload).encode()).hexdigest()
