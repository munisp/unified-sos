"""Domain models for mod-citizen-portal (SOS module — CIT-11).

Unified Citizen Portal, Sovereign Identity SSO & Civil Service Clean-Up.
This module complements mod-identity (the governed verification API): the
portal owns the citizen-facing self-service surface, the Keycloak OIDC SSO
facade, e-petitions, and the civil-service biometric / payroll clean-up
pipeline.

Privacy posture enforced in code:
- **NIN never stored raw** — wallets carry ``nin_hash`` (SHA-256) only and
  read models return a masked form; the raw NIN exists only in the create
  request body.
- **No raw biometrics** — only biometric template hashes and boolean
  liveness/verification outcomes are persisted.

Monetary amounts are integer kobo. Settlement splits default to 70% state /
15% MDA / 15% platform [DERIVED], overridable per state config.
"""
from __future__ import annotations

import enum
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_nin(nin: str) -> str:
    """One-way NIN hash; raw NINs are never persisted or returned."""
    return hashlib.sha256(f"nin:{nin}".encode()).hexdigest()


STATE_IDS = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")


# --------------------------------------------------------------------------
# 1. Citizen identity wallet / SSO facade
# --------------------------------------------------------------------------


class WalletStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class IdentityWallet(BaseModel):
    """Citizen identity wallet — state ID mapped to a NIN hash.

    The raw NIN is accepted only at creation time (``WalletCreate`` in
    main.py) and immediately reduced to ``nin_hash``; no read path can
    reconstruct or expose it.
    """

    wallet_id: str
    state_id: str = Field(description="tenant: lagos | ogun | osun | benue | nasarawa | taraba")
    nin_hash: str = Field(description="SHA-256 of the citizen NIN; raw NIN never stored")
    keycloak_realm: str = Field(description="OIDC realm for this state tenant, e.g. sos-lagos")
    keycloak_client_id: str = Field(default="citizen-portal")
    status: WalletStatus = WalletStatus.ACTIVE
    created_at: datetime = Field(default_factory=utcnow)

    def masked_nin(self) -> str:
        """Masked NIN representation — hash prefix only, never the raw NIN."""
        return f"NIN-HASH:{self.nin_hash[:8]}…"


class SsoSessionStatus(str, enum.Enum):
    PENDING = "PENDING"
    AUTHENTICATED = "AUTHENTICATED"
    EXPIRED = "EXPIRED"


class SsoSession(BaseModel):
    """OIDC authorization/session descriptor (Keycloak facade).

    In production the portal redirects to the state realm's Keycloak
    authorization endpoint; this record is the server-side handle carrying
    redirect URI, scopes, expiry, and realm.
    """

    session_id: str
    state_id: str
    wallet_id: str
    realm: str
    client_id: str
    redirect_uri: str
    scopes: List[str] = Field(default_factory=lambda: ["openid", "profile"])
    status: SsoSessionStatus = SsoSessionStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return (now or utcnow()) >= self.expires_at


# --------------------------------------------------------------------------
# 2. Multi-MDA self-service
# --------------------------------------------------------------------------


class ServiceCategory(str, enum.Enum):
    REVENUE = "REVENUE"
    LANDS = "LANDS"
    HEALTH = "HEALTH"
    EDUCATION = "EDUCATION"
    MARKET = "MARKET"
    MINING = "MINING"
    AGRICULTURE = "AGRICULTURE"
    TRANSPORT = "TRANSPORT"
    ENVIRONMENT = "ENVIRONMENT"
    FORESTRY = "FORESTRY"
    INVESTMENT = "INVESTMENT"


class ServiceCatalogEntry(BaseModel):
    """One MDA service offered through the portal, seeded per state.

    ``module`` names the owning SOS module (e.g. ``mod-mining``); the
    :mod:`app.catalog` providers use it to filter the catalog against the
    state's enabled-module config. ``endpoint_hint`` is the owning module's
    API hint used by channel adapters (USSD/IVR) when deep-linking.
    """

    service_code: str
    state_id: str
    name: str
    category: ServiceCategory
    mda: str
    base_fee_kobo: int = 0
    expedited_fee_kobo: int = 0
    sla_days: int = 14
    active: bool = True
    module: str = Field(default="", description="owning SOS module, e.g. mod-mining")
    endpoint_hint: str = Field(default="", description="owning module API hint for channel deep-links")


class Priority(str, enum.Enum):
    STANDARD = "STANDARD"
    EXPEDITED = "EXPEDITED"


class RequestStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


class StatusEvent(BaseModel):
    status: str
    at: datetime = Field(default_factory=utcnow)
    note: str = ""


class ServiceRequest(BaseModel):
    """Citizen self-service request with dynamic form payload and timeline."""

    request_id: str
    state_id: str
    wallet_id: str
    service_code: str
    form_payload: Dict[str, str] = Field(default_factory=dict)
    priority: Priority = Priority.STANDARD
    fee_kobo: int = 0
    status: RequestStatus = RequestStatus.SUBMITTED
    timeline: List[StatusEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 3. E-petitions
# --------------------------------------------------------------------------


class PetitionStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class Petition(BaseModel):
    """E-petition intake + workflow; ``reference_id`` is the public handle."""

    petition_id: str
    reference_id: str = Field(description="public reference, e.g. PET-LAGOS-000001")
    state_id: str
    wallet_id: str
    title: str
    body: str
    status: PetitionStatus = PetitionStatus.SUBMITTED
    timeline: List[StatusEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 4. Civil-service biometric clean-up
# --------------------------------------------------------------------------


class ServantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RETIRED = "RETIRED"
    SUSPENDED = "SUSPENDED"


class CivilServant(BaseModel):
    """Civil-service payroll record. Hashes only — no raw biometrics."""

    employee_no: str
    state_id: str
    full_name: str
    mda: str
    grade_band: str
    salary_kobo: int = Field(ge=0)
    biometric_template_hash: str
    bank_account_hash: str
    status: ServantStatus = ServantStatus.ACTIVE
    registered_at: datetime = Field(default_factory=utcnow)


class BiometricVerification(BaseModel):
    """Proof-of-liveness / verification result; no raw biometric data."""

    verification_id: str
    state_id: str
    employee_no: str
    liveness_passed: bool
    verified: bool
    verified_at: datetime = Field(default_factory=utcnow)


class GhostRule(str, enum.Enum):
    """Deterministic ghost-worker detection rules."""

    UNVERIFIED_BIOMETRIC = "UNVERIFIED_BIOMETRIC"
    DUPLICATE_BIOMETRIC = "DUPLICATE_BIOMETRIC"
    DUPLICATE_SALARY_ACCOUNT = "DUPLICATE_SALARY_ACCOUNT"
    INACTIVE_STILL_PAID = "INACTIVE_STILL_PAID"


class GhostWorkerFinding(BaseModel):
    finding_id: str
    state_id: str
    rule: GhostRule
    employee_nos: List[str]
    recoverable_kobo: int = Field(ge=0)
    detail: str = ""


class TemporalWorkflowRef(BaseModel):
    """Placeholder reference to the durable Temporal cleanup workflow.

    Production: one Temporal workflow per payroll audit drives biometric
    re-verification notices, payroll holds, and recovery postings; this
    reference module records the handle only.
    """

    workflow_id: str
    task_queue: str = "payroll-cleanup"
    status: str = "PLACEHOLDER"
    note: str = "durable cleanup workflow scheduled via Temporal in production"


class PayrollAudit(BaseModel):
    """One deterministic ghost-worker audit run over a state's payroll."""

    audit_id: str
    state_id: str
    run_at: datetime = Field(default_factory=utcnow)
    findings: List[GhostWorkerFinding] = Field(default_factory=list)
    total_recoverable_kobo: int = 0
    temporal_workflow: Optional[TemporalWorkflowRef] = None


# --------------------------------------------------------------------------
# 5. Fees / settlement
# --------------------------------------------------------------------------


class SettlementLine(BaseModel):
    """One settlement leg (ledger chart of accounts mapping in README)."""

    payee: str = Field(description="state_tsa | mda_vote | platform_escrow")
    share_bps: int
    amount_kobo: int = Field(ge=0)


class SettlementRecord(BaseModel):
    settlement_id: str
    state_id: str
    source: str = Field(description="e.g. SMARTCARD_FEE, EXPEDITED_FEE")
    reference_id: str = Field(description="originating request/wallet id")
    gross_kobo: int = Field(ge=0)
    lines: List[SettlementLine]
    settled_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# 6. Citizen channels (USSD / IVR)
# --------------------------------------------------------------------------


def hash_msisdn(msisdn: str) -> str:
    """One-way MSISDN hash; raw phone numbers are never persisted."""
    return hashlib.sha256(f"msisdn:{msisdn}".encode()).hexdigest()


def hash_pin(pin: str, msisdn_hash: str) -> str:
    """One-way channel PIN hash, salted with the caller's MSISDN hash.

    Same idiom as :func:`hash_nin`: raw PINs are never persisted, and the
    salt binds the credential to one caller so a leaked hash cannot be
    replayed for another MSISDN.
    """
    return hashlib.sha256(f"pin:{msisdn_hash}:{pin}".encode()).hexdigest()


def hash_gateway_token(token: str) -> str:
    """One-way hash of the gateway-provided session token (never stored raw)."""
    return hashlib.sha256(f"gwtoken:{token}".encode()).hexdigest()


class BindingStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ChannelAuth(BaseModel):
    """Per-caller USSD/IVR credential record (SIM-swap defence).

    Keyed by (state_id, msisdn_hash); survives individual channel sessions.
    Three consecutive PIN failures lock the credential (fail-closed).
    """

    state_id: str
    msisdn_hash: str
    pin_hash: str
    failed_attempts: int = 0
    locked: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class WalletBinding(BaseModel):
    """Binds a wallet to a channel identifier (hashed MSISDN).

    Account recovery (wallet rebind) suspends the old binding and creates a
    new one; a suspended binding can never authenticate the wallet again.
    """

    state_id: str
    wallet_id: str
    msisdn_hash: str
    status: BindingStatus = BindingStatus.ACTIVE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PortalAuditEvent(BaseModel):
    """Append-only, hash-chained audit record (mod-identity idiom).

    ``entry_hash`` = sha256(prev_hash + canonical payload). The repository
    exposes no update or delete path.
    """

    seq: int
    action: str
    state_id: str
    actor_id: str
    subject_id: str
    details: str = ""
    prev_hash: str
    entry_hash: str
    at: datetime = Field(default_factory=utcnow)

    @staticmethod
    def compute_hash(prev_hash: str, payload: str) -> str:
        return hashlib.sha256((prev_hash + "|" + payload).encode()).hexdigest()


class ChannelKind(str, enum.Enum):
    USSD = "USSD"
    IVR = "IVR"


class ChannelSession(BaseModel):
    """Server-side USSD/IVR session state.

    Carries the hashed MSISDN only (never the raw number), the current menu
    node, the selection breadcrumb trail, and last-activity for the 180s
    deterministic timeout.
    """

    session_id: str
    state_id: str
    msisdn_hash: str
    channel: ChannelKind = ChannelKind.USSD
    node: str = Field(default="root", description="root | category | service | confirm | pin_set | pin_confirm | pin_verify | done")
    depth: int = 0
    selections: List[str] = Field(default_factory=list)
    pin_verified: bool = Field(default=False, description="PIN verified this session (sensitive nodes gate on it)")
    pending_pin_hash: Optional[str] = Field(default=None, description="first leg of PIN registration, hash-only")
    gateway_token_hash: Optional[str] = Field(default=None, description="hash of the gateway session token this session is bound to")
    created_at: datetime = Field(default_factory=utcnow)
    last_activity: datetime = Field(default_factory=utcnow)


# Default split [DERIVED]: 70% state / 15% MDA / 15% platform.
DEFAULT_SPLIT_BPS = {"state_tsa": 7_000, "mda_vote": 1_500, "platform_escrow": 1_500}

DEFAULT_SSO_SESSION_TTL = timedelta(minutes=15)
