"""Public read-models for mod-transparency (P2 Workstream E).

Every model here is a *structurally redacted* projection of data owned by
source modules (mod-police-cad trust fund, mod-ppp-investment concession
escrow + procurement audit chain). By construction there are no PII fields:
no donor names/references, no actor identities, no free-text details that
could carry personal data. Identities are reduced to salted SHA-256
pseudonyms; amounts are integer kobo; every projection carries a hash-chain
cursor so the public can detect tampering without seeing the raw record.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


#: Tenant states served by the platform. Unknown ids are answered 404 with a
#: generic body — the transparency API never enumerates which states exist.
KNOWN_STATE_IDS: tuple[str, ...] = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")

#: Chain genesis mirrors mod-ppp-investment's audit chain so a procurement
#: digest verified here is verified against the same anchor as the source.
AUDIT_GENESIS_HASH = hashlib.sha256(b"mod-ppp-investment-audit-genesis").hexdigest()


class TrustFundEntryKind(str, Enum):
    DONATION = "donation"
    DISBURSEMENT = "disbursement"


class TrustFundEntry(BaseModel):
    """One public trust-fund movement (donation in / disbursement out).

    ``donor_alias_hash`` is a one-way pseudonym (SHA-256 of the source
    ``donor_ref``); the raw reference never crosses the transparency
    boundary. ``cursor`` chains entries per state so deletions/reorders are
    detectable.
    """

    entry_id: str
    kind: TrustFundEntryKind
    amount_kobo: int = Field(..., gt=0)
    donor_alias_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 pseudonym of the donor reference (donations only)",
    )
    purpose_label: Optional[str] = Field(
        default=None, description="Public purpose label (disbursements only)"
    )
    occurred_at: str
    cursor: str = Field(description="hash-chain cursor: sha256(prev_cursor|entry payload)")


class TrustFundFeed(BaseModel):
    """Public trust-fund feed for one tenant state."""

    state_id: str
    balance_kobo: int = Field(ge=0)
    entries: list[TrustFundEntry]
    head_cursor: str = Field(description="cursor of the latest entry; empty-chain genesis otherwise")


class ConcessionEscrowStatement(BaseModel):
    """Public projection of a monthly concession revenue-share settlement
    (mod-ppp-investment SettlementStatement). Kobo amounts only; contract and
    concessionaire identities are pseudonymised."""

    statement_id: str
    contract_ref_hash: str = Field(description="SHA-256 pseudonym of the contract id")
    period: str = Field(description="settlement period, e.g. 2026-01")
    gross_collections_kobo: int = Field(ge=0)
    state_share_kobo: int = Field(ge=0)
    concessionaire_share_kobo: int = Field(ge=0)
    applied_state_share_bps: int = Field(ge=0, le=10_000)
    reconciled: bool
    cursor: str = Field(description="hash-chain cursor over the state's statement feed")


class ProcurementAuditDigest(BaseModel):
    """Redacted digest of one procurement audit-chain entry.

    Actor identity and free-text details are removed; the digest keeps only
    what is needed to recompute the hash chain: seq, action, subject
    pseudonym and the link hashes.
    """

    seq: int = Field(ge=1)
    action: str
    subject_ref_hash: str = Field(description="SHA-256 pseudonym of the audited subject")
    prev_hash: str
    entry_hash: str
    at: datetime


class ProcurementAuditVerification(BaseModel):
    """Result of recomputing the procurement hash chain for a tenant state."""

    state_id: str
    chain_valid: bool
    entries_checked: int = Field(ge=0)
    head_hash: str = Field(description="entry_hash of the state's latest audit entry")
    first_invalid_seq: Optional[int] = Field(
        default=None, description="seq of the first entry failing verification, if any"
    )


def compute_entry_hash(prev_hash: str, payload: str) -> str:
    """Hash-chain link, identical construction to mod-ppp-investment
    ``AuditEntry.compute_hash`` so verification is interoperable."""
    return hashlib.sha256((prev_hash + "|" + payload).encode()).hexdigest()


def audit_digest_payload(digest: ProcurementAuditDigest) -> str:
    """Canonical payload for digest chain verification."""
    return f"{digest.seq}|{digest.action}|{digest.subject_ref_hash}"


def verify_digest_chain(digests: list[ProcurementAuditDigest],
                        genesis: str = AUDIT_GENESIS_HASH) -> tuple[bool, Optional[int]]:
    """Recompute a per-state digest chain; returns (valid, first_invalid_seq)."""
    prev = genesis
    for d in digests:
        if d.prev_hash != prev:
            return False, d.seq
        if d.entry_hash != compute_entry_hash(prev, audit_digest_payload(d)):
            return False, d.seq
        prev = d.entry_hash
    return True, None
