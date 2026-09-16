"""Domain: mortgage & lien lifecycle for state land registries.

Lifecycle::

    APPLICATION → KYC_CREDIT_REVIEW → APPROVED | DECLINED
                                    ↘ MANUAL_REVIEW → APPROVED (officer)
    APPROVED → LIEN_REGISTERED → DISBURSED → ACTIVE → DISCHARGED
                                             ↘ DEFAULTED → FORECLOSED
    FORECLOSED → POSSESSION_REGISTERED → SALE_AUTHORIZED → SOLD
             → PROCEEDS_DISTRIBUTED → CLOSED

Invariants enforced here:

* All money is integer kobo — never floats. The annuity schedule conserves
  value exactly: ``sum(installments) == principal + interest`` with the
  rounding remainder carried on the final installment.
* Disbursement and repayment move funds through the two-phase ledger seam
  (PENDING hold → POST, VOID on failure) with deterministic 128-bit ids —
  mirroring ``ledger/fundsflow/tigerbeetle_flows.py``.
* One active lien per parcel, unless ``second_charge=true`` with an explicit
  ``senior_lien_id`` (priority ordering, max 2 liens).
* Full repayment discharges the mortgage and releases the lien atomically
  in the same domain call — if the ledger post fails there is no discharge.
* Every transition and every payment is appended to a hash-chained audit
  log (``services/_shared/hashchain``, P1 audit immutability).

All state is tenant-scoped (``X-State-Tenant``); cross-tenant access raises
:class:`CrossTenantError` (mapped to HTTP 404 at the API layer).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from fractions import Fraction
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .adapters import (
    CreditScoringAdapter,
    FixtureCreditScorer,
    FixtureLedgerAdapter,
    FixtureLandRegistry,
    FixtureTitleTransfer,
    LandRegistryAdapter,
    LandTitleTransferAdapter,
    MortgageLedgerAdapter,
    deterministic_account_id,
    deterministic_transfer_id,
)
from . import events as ev
from .outbox import InMemoryOutbox

# --- hash-chained audit: reuse services/_shared/hashchain, local fallback ----
try:
    from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash, verify_event_chain
except ImportError:  # minimal container images ship only the app package
    import hashlib
    import json

    GENESIS_PREV_HASH = "0" * 64

    def event_payload_hash(payload, prev_hash):  # type: ignore[no-redef]
        body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
        body["prev_hash"] = prev_hash
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def verify_event_chain(events):  # type: ignore[no-redef]
        errors: list[str] = []
        last = None
        for i, event in enumerate(events):
            prev = event.get("prev_hash")
            expected = GENESIS_PREV_HASH if last is None else last
            if prev != expected:
                errors.append(f"event {i}: broken chain link")
            if event.get("event_hash") != event_payload_hash(event, prev or ""):
                errors.append(f"event {i}: hash mismatch — record tampered")
            last = event.get("event_hash")
        return errors


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _add_months(dt: datetime, months: int) -> datetime:
    """Calendar-month addition with day clamping (deterministic due dates)."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return dt.replace(year=year, month=month, day=min(dt.day, days_in_month))


class CrossTenantError(ValueError):
    """Tenant-isolation violation — mapped to HTTP 404 at the API layer
    (fail-closed: a foreign tenant's mortgage simply does not exist)."""


class InvalidTransitionError(ValueError):
    """Illegal lifecycle transition — mapped to HTTP 409 at the API layer."""


class ConflictError(ValueError):
    """Idempotency/lien conflict — mapped to HTTP 409 at the API layer."""


class NotFoundError(KeyError):
    """Missing mortgage/lien — mapped to HTTP 404 at the API layer."""


# --- credit policy ------------------------------------------------------------
CREDIT_APPROVE_MIN = 600
CREDIT_MANUAL_REVIEW_MIN = 550
TERM_MONTHS_MIN = 6
TERM_MONTHS_MAX = 360
DEFAULT_DAYS_PAST_DUE = 90
MAX_LIENS_PER_PARCEL = 2


class MortgageStatus(str, Enum):
    APPLICATION = "APPLICATION"
    KYC_CREDIT_REVIEW = "KYC_CREDIT_REVIEW"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    LIEN_REGISTERED = "LIEN_REGISTERED"
    DISBURSED = "DISBURSED"
    ACTIVE = "ACTIVE"
    DISCHARGED = "DISCHARGED"
    DEFAULTED = "DEFAULTED"
    FORECLOSED = "FORECLOSED"
    POSSESSION_REGISTERED = "POSSESSION_REGISTERED"
    SALE_AUTHORIZED = "SALE_AUTHORIZED"
    SOLD = "SOLD"
    PROCEEDS_DISTRIBUTED = "PROCEEDS_DISTRIBUTED"
    CLOSED = "CLOSED"


class LienStatus(str, Enum):
    REGISTERED = "REGISTERED"
    RELEASED = "RELEASED"


class Lien(BaseModel):
    lien_id: str
    tenant_state_id: str
    mortgage_id: str
    parcel_id: str
    title_ref: str
    priority: int = Field(..., ge=1, le=MAX_LIENS_PER_PARCEL)  # 1 = senior
    second_charge: bool = False
    senior_lien_id: Optional[str] = None
    status: LienStatus = LienStatus.REGISTERED
    title_hash: Optional[str] = None  # hash of the verified title snapshot
    registered_at: str
    released_at: Optional[str] = None


class Installment(BaseModel):
    """One annuity installment; amounts integer kobo, interest-then-principal."""

    seq: int
    due_date: str
    amount_kobo: int
    interest_kobo: int
    principal_kobo: int
    paid_interest_kobo: int = 0
    paid_principal_kobo: int = 0

    @property
    def outstanding_kobo(self) -> int:
        return self.amount_kobo - self.paid_interest_kobo - self.paid_principal_kobo

    @property
    def settled(self) -> bool:
        return self.outstanding_kobo == 0


class Payment(BaseModel):
    payment_id: str
    idempotency_key: str
    amount_kobo: int
    interest_kobo: int
    principal_kobo: int  # scheduled principal + overpayment principal
    overpayment_kobo: int = 0  # excess routed to the borrower credit account
    transfer_id: str
    applied_at: str


class Mortgage(BaseModel):
    mortgage_id: str
    tenant_state_id: str
    applicant_id: str
    parcel_id: str
    title_ref: str
    principal_kobo: int = Field(..., gt=0)
    rate_bps: int = Field(..., ge=0, description="annual interest, basis points")
    term_months: int = Field(..., ge=TERM_MONTHS_MIN, le=TERM_MONTHS_MAX)
    status: MortgageStatus = MortgageStatus.APPLICATION
    credit_score: Optional[int] = None
    approved_by: Optional[str] = None
    approval_reason: Optional[str] = None
    lien_id: Optional[str] = None
    schedule: List[Installment] = []
    payments: List[Payment] = []
    outstanding_principal_kobo: int = 0
    disbursement_transfer_id: Optional[str] = None
    disbursed_at: Optional[str] = None
    discharged_at: Optional[str] = None
    defaulted_at: Optional[str] = None
    foreclosed_at: Optional[str] = None
    foreclosure_reason: Optional[str] = None
    credit_balance_kobo: int = 0  # borrower credit from accepted overpayments
    credit_refund_transfer_id: Optional[str] = None
    # foreclosure pipeline (FORECLOSED → ... → CLOSED)
    possession_reference: Optional[str] = None
    possession_registered_at: Optional[str] = None
    valuation_kobo: Optional[int] = None
    reserve_price_kobo: Optional[int] = None
    valuer_id: Optional[str] = None
    sale_authorized_at: Optional[str] = None
    purchaser_id: Optional[str] = None
    gross_proceeds_kobo: Optional[int] = None
    sale_costs_kobo: Optional[int] = None
    title_transfer_ref: Optional[str] = None
    sale_transfer_id: Optional[str] = None
    sold_at: Optional[str] = None
    distribution: Optional[dict] = None  # waterfall legs, integer kobo
    deficiency_kobo: int = 0
    proceeds_distributed_at: Optional[str] = None
    closed_at: Optional[str] = None
    created_at: str

    @property
    def paid_to_date_kobo(self) -> int:
        return sum(p.amount_kobo for p in self.payments)

    @property
    def total_interest_kobo(self) -> int:
        return sum(i.interest_kobo for i in self.schedule)

    def days_past_due(self, now: datetime) -> int:
        for inst in self.schedule:
            if not inst.settled:
                due = datetime.fromisoformat(inst.due_date)
                return max(0, (now - due).days)
        return 0


def annuity_schedule(
    principal_kobo: int, rate_bps: int, term_months: int, start: datetime
) -> List[Installment]:
    """Equal-installment (annuity) schedule in exact integer kobo.

    Computed with :class:`fractions.Fraction` (no floats): every installment
    except the last is the floored level payment; the final installment
    carries the rounding remainder, so
    ``sum(i.amount_kobo) == principal + sum(i.interest_kobo)`` exactly.
    """
    if not (TERM_MONTHS_MIN <= term_months <= TERM_MONTHS_MAX):
        raise ValueError(f"term_months must be in [{TERM_MONTHS_MIN}, {TERM_MONTHS_MAX}]")
    if principal_kobo <= 0:
        raise ValueError("principal_kobo must be positive")
    r = Fraction(rate_bps, 120_000)  # monthly rate: bps / 10000 / 12
    n = term_months
    if r == 0:
        level = Fraction(principal_kobo, n)
    else:
        q = (1 + r) ** n
        level = Fraction(principal_kobo) * r * q / (q - 1)
    level_kobo = int(level)  # floor; remainder lands on the final installment

    installments: List[Installment] = []
    balance = principal_kobo
    for seq in range(1, n + 1):
        interest = int(Fraction(balance) * r)  # floor to kobo
        if seq < n:
            principal_part = min(level_kobo - interest, balance)
            amount = interest + principal_part
        else:  # final installment takes the exact remainder
            principal_part = balance
            amount = interest + principal_part
        balance -= principal_part
        installments.append(Installment(
            seq=seq,
            due_date=_iso(_add_months(start, seq)),
            amount_kobo=amount,
            interest_kobo=interest,
            principal_kobo=principal_part,
        ))
    assert balance == 0, "annuity schedule must fully amortize the principal"
    return installments


class TenantState:
    def __init__(self, tenant_state_id: str) -> None:
        self.tenant_state_id = tenant_state_id
        self.mortgages: Dict[str, Mortgage] = {}
        self.liens: Dict[str, Lien] = {}
        self.audit: Dict[str, List[dict]] = {}  # mortgage_id -> hash chain
        self.payment_keys: Dict[str, Payment] = {}  # mortgage_id|key -> payment


class MortgageStore:
    """Tenant-scoped mortgage/lien store with fail-closed adapter seams."""

    def __init__(
        self,
        scorer: Optional[CreditScoringAdapter] = None,
        ledger: Optional[MortgageLedgerAdapter] = None,
        lands: Optional[LandRegistryAdapter] = None,
        title_transfer: Optional[LandTitleTransferAdapter] = None,
        outbox: Optional[InMemoryOutbox] = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.scorer = scorer or FixtureCreditScorer()
        self.ledger = ledger or FixtureLedgerAdapter()
        self.lands = lands or FixtureLandRegistry()
        self.title_transfer = title_transfer or FixtureTitleTransfer()
        self.outbox = outbox or InMemoryOutbox()
        self.clock = clock
        self._lock = threading.Lock()
        self._tenants: Dict[str, TenantState] = {}

    # -- plumbing --------------------------------------------------------------
    def tenant(self, tenant_state_id: str) -> TenantState:
        return self._tenants.setdefault(tenant_state_id.lower(), TenantState(tenant_state_id.lower()))

    def _now(self) -> datetime:
        return self.clock()

    def _audit(self, state: TenantState, mortgage_id: str, kind: str, **payload) -> dict:
        chain = state.audit.setdefault(mortgage_id, [])
        prev = chain[-1]["event_hash"] if chain else GENESIS_PREV_HASH
        record = {
            "kind": kind,
            "mortgage_id": mortgage_id,
            "tenant_state_id": state.tenant_state_id,
            "at": _iso(self._now()),
            **payload,
        }
        record["prev_hash"] = prev
        record["event_hash"] = event_payload_hash(record, prev)
        chain.append(record)
        return record

    def _emit(self, topic: str, idempotency_key: str, **payload) -> None:
        """Append an outbox row atomically with the domain transition
        (caller holds the lock). The API layer NEVER publishes directly —
        :class:`mortgage_app.outbox.OutboxRelay` delivers and acks."""
        self.outbox.append(topic, payload, idempotency_key)

    def audit_feed(self, tenant_state_id: str, mortgage_id: str) -> List[dict]:
        state = self.tenant(tenant_state_id)
        self._get(state, mortgage_id)
        return list(state.audit.get(mortgage_id, []))

    def verify_audit_chain(self, tenant_state_id: str, mortgage_id: str) -> List[str]:
        state = self.tenant(tenant_state_id)
        self._get(state, mortgage_id)
        return verify_event_chain(state.audit.get(mortgage_id, []))

    def _get(self, state: TenantState, mortgage_id: str) -> Mortgage:
        m = state.mortgages.get(mortgage_id)
        if m is None:
            raise NotFoundError(f"mortgage {mortgage_id} not found")
        return m

    def get_mortgage(self, tenant_state_id: str, mortgage_id: str) -> Mortgage:
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            self._evaluate_default(state, m)
            return m

    # -- application ----------------------------------------------------------
    def apply(
        self, tenant_state_id: str, applicant_id: str, parcel_id: str,
        title_ref: str, principal_kobo: int, rate_bps: int, term_months: int,
    ) -> Mortgage:
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = Mortgage(
                mortgage_id=_id("mtg"),
                tenant_state_id=state.tenant_state_id,
                applicant_id=applicant_id,
                parcel_id=parcel_id,
                title_ref=title_ref,
                principal_kobo=principal_kobo,
                rate_bps=rate_bps,
                term_months=term_months,
                outstanding_principal_kobo=principal_kobo,
                created_at=_iso(self._now()),
            )
            state.mortgages[m.mortgage_id] = m
            self._audit(state, m.mortgage_id, "application_received",
                        applicant_id=applicant_id, parcel_id=parcel_id,
                        principal_kobo=principal_kobo)
            self._emit(ev.EVENT_APPLICATION_RECEIVED, f"{m.mortgage_id}|application_received",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.created_at, applicant_id=applicant_id,
                       parcel_id=parcel_id, principal_kobo=principal_kobo)
            return m

    def list_mortgages(
        self, tenant_state_id: str, status: Optional[MortgageStatus] = None,
        applicant_id: Optional[str] = None, parcel_id: Optional[str] = None,
    ) -> List[Mortgage]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            out = list(state.mortgages.values())
            if status is not None:
                out = [m for m in out if m.status is status]
            if applicant_id:
                out = [m for m in out if m.applicant_id == applicant_id]
            if parcel_id:
                out = [m for m in out if m.parcel_id == parcel_id]
            return out

    # -- credit review -----------------------------------------------------------
    def credit_review(self, tenant_state_id: str, mortgage_id: str) -> Mortgage:
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.APPLICATION:
                raise InvalidTransitionError(
                    f"credit review requires APPLICATION, got {m.status.value}")
            m.status = MortgageStatus.KYC_CREDIT_REVIEW
            self._audit(state, m.mortgage_id, "transition", to=m.status.value)
            score = self.scorer.score(m.applicant_id)
            m.credit_score = score
            if score >= CREDIT_APPROVE_MIN:
                m.status = MortgageStatus.APPROVED
                outcome = "approved"
            elif score >= CREDIT_MANUAL_REVIEW_MIN:
                m.status = MortgageStatus.MANUAL_REVIEW
                outcome = "manual_review"
            else:
                m.status = MortgageStatus.DECLINED
                outcome = "declined"
            self._audit(state, m.mortgage_id, "credit_scored",
                        score=score, outcome=outcome, to=m.status.value)
            now = _iso(self._now())
            self._emit(ev.EVENT_CREDIT_SCORED, f"{m.mortgage_id}|credit_scored",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=now, applicant_id=m.applicant_id,
                       score=score, outcome=outcome)
            if m.status is MortgageStatus.APPROVED:
                self._emit(ev.EVENT_APPROVED, f"{m.mortgage_id}|approved",
                           tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                           occurred_at=now, officer="", reason="")
            elif m.status is MortgageStatus.DECLINED:
                self._emit(ev.EVENT_DECLINED, f"{m.mortgage_id}|declined",
                           tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                           occurred_at=now, score=score)
            return m

    def approve_manual(
        self, tenant_state_id: str, mortgage_id: str, officer: str, reason: str
    ) -> Mortgage:
        if not officer or not reason:
            raise ValueError("manual approval requires an officer and a reason")
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.MANUAL_REVIEW:
                raise InvalidTransitionError(
                    f"manual approval requires MANUAL_REVIEW, got {m.status.value}")
            m.status = MortgageStatus.APPROVED
            m.approved_by = officer
            m.approval_reason = reason
            self._audit(state, m.mortgage_id, "approved",
                        officer=officer, reason=reason, manual=True)
            self._emit(ev.EVENT_APPROVED, f"{m.mortgage_id}|approved",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=_iso(self._now()), officer=officer, reason=reason)
            return m

    # -- lien registration ---------------------------------------------------------
    def register_lien(
        self, tenant_state_id: str, mortgage_id: str,
        second_charge: bool = False, senior_lien_id: Optional[str] = None,
    ) -> Lien:
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.APPROVED:
                raise InvalidTransitionError(
                    f"lien registration requires APPROVED, got {m.status.value}")
            snap = self.lands.title_snapshot(state.tenant_state_id, m.parcel_id)
            if snap is None or snap.status == "not_found":
                raise NotFoundError(
                    f"parcel {m.parcel_id} / title {m.title_ref} not found in land registry")
            # Fail-closed title verification (P1): exact title_ref match,
            # applicant == registered owner, status current, no blocking
            # encumbrance. UNVERIFIED (field absent) is a hard failure for
            # strict (production) adapters; the fixture is lenient.
            strict = bool(getattr(self.lands, "strict", True))
            unverified: List[str] = []
            if snap.status == "unverified":
                unverified.append("status")
            elif snap.status != "current":
                raise ConflictError(
                    f"title for parcel {m.parcel_id} is not current "
                    f"(status={snap.status})")
            if snap.current_title_ref is None:
                unverified.append("title_ref")
            elif snap.current_title_ref != m.title_ref:
                raise ConflictError(
                    f"title_ref mismatch: registry holds {snap.current_title_ref!r}, "
                    f"application claims {m.title_ref!r}")
            if snap.owner_id is None:
                unverified.append("owner")
            elif snap.owner_id != m.applicant_id:
                raise ConflictError(
                    f"applicant {m.applicant_id!r} is not the registered title owner "
                    f"({snap.owner_id!r})")
            if snap.has_blocking_encumbrance is None:
                unverified.append("encumbrance")
            elif snap.has_blocking_encumbrance:
                raise ConflictError(
                    f"parcel {m.parcel_id} has a blocking encumbrance on title")
            if unverified and strict:
                raise ConflictError(
                    "title UNVERIFIED — registry could not attest "
                    + ", ".join(unverified)
                    + " (fail-closed: refusing to register the lien)")
            title_hash = snap.title_hash()
            active = [
                l for l in state.liens.values()
                if l.parcel_id == m.parcel_id and l.status is LienStatus.REGISTERED
            ]
            if not second_charge:
                if active:
                    raise ConflictError(
                        f"parcel {m.parcel_id} already has an active lien "
                        f"({active[0].lien_id}); use second_charge with senior_lien_id")
                priority = 1
            else:
                senior = state.liens.get(senior_lien_id or "")
                if senior is None or senior.parcel_id != m.parcel_id \
                        or senior.status is not LienStatus.REGISTERED:
                    raise ConflictError(
                        "second_charge requires an active senior_lien_id on the same parcel")
                if len(active) >= MAX_LIENS_PER_PARCEL:
                    raise ConflictError(
                        f"parcel {m.parcel_id} already has {MAX_LIENS_PER_PARCEL} active liens (max)")
                priority = 2
            lien = Lien(
                lien_id=_id("lien"),
                tenant_state_id=state.tenant_state_id,
                mortgage_id=m.mortgage_id,
                parcel_id=m.parcel_id,
                title_ref=m.title_ref,
                priority=priority,
                second_charge=second_charge,
                senior_lien_id=senior_lien_id if second_charge else None,
                title_hash=title_hash,
                registered_at=_iso(self._now()),
            )
            state.liens[lien.lien_id] = lien
            m.lien_id = lien.lien_id
            m.status = MortgageStatus.LIEN_REGISTERED
            self._audit(state, m.mortgage_id, "lien_registered",
                        lien_id=lien.lien_id, parcel_id=lien.parcel_id,
                        priority=priority, title_hash=title_hash,
                        to=m.status.value)
            self._emit(ev.EVENT_LIEN_REGISTERED, f"{m.mortgage_id}|lien_registered|{lien.lien_id}",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=lien.registered_at, lien_id=lien.lien_id,
                       parcel_id=lien.parcel_id, title_ref=lien.title_ref,
                       priority=priority)
            return lien

    def list_liens(self, tenant_state_id: str, parcel_id: Optional[str] = None) -> List[Lien]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            liens = list(state.liens.values())
            if parcel_id:
                liens = [l for l in liens if l.parcel_id == parcel_id]
            return sorted(liens, key=lambda l: (l.parcel_id, l.priority, l.registered_at))

    # -- disbursement ---------------------------------------------------------------
    def disbursement_idempotency_key(self, mortgage_id: str) -> str:
        import hashlib

        return hashlib.sha256(f"{mortgage_id}|disbursement".encode()).hexdigest()

    def treasury_account(self, tenant_state_id: str) -> int:
        return deterministic_account_id("treasury", tenant_state_id)

    def applicant_account(self, applicant_id: str) -> int:
        return deterministic_account_id("applicant", applicant_id)

    def disburse(self, tenant_state_id: str, mortgage_id: str) -> Mortgage:
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status in (MortgageStatus.DISBURSED, MortgageStatus.ACTIVE):
                return m  # idempotent replay: deterministic key already posted
            if m.status is not MortgageStatus.LIEN_REGISTERED:
                raise InvalidTransitionError(
                    f"disbursement requires LIEN_REGISTERED, got {m.status.value}")
            key = self.disbursement_idempotency_key(m.mortgage_id)
            transfer_id = deterministic_transfer_id(key, "disbursement")
            treasury = self.treasury_account(state.tenant_state_id)
            borrower = self.applicant_account(m.applicant_id)
            # two-phase: PENDING hold → POST; VOID on failure (saga compensation)
            self.ledger.hold(transfer_id, treasury, borrower, m.principal_kobo, key)
            try:
                self.ledger.post(transfer_id)
            except Exception:
                self.ledger.void(transfer_id)
                raise
            # conservation of value: disbursed == approved principal, integer kobo
            assert m.principal_kobo > 0
            m.disbursement_transfer_id = str(transfer_id)
            m.disbursed_at = _iso(self._now())
            m.schedule = annuity_schedule(
                m.principal_kobo, m.rate_bps, m.term_months, self._now())
            m.status = MortgageStatus.DISBURSED
            self._audit(state, m.mortgage_id, "disbursed",
                        amount_kobo=m.principal_kobo, transfer_id=str(transfer_id),
                        idempotency_key=key, to=m.status.value)
            self._emit(ev.EVENT_DISBURSED, f"{m.mortgage_id}|disbursed",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.disbursed_at, amount_kobo=m.principal_kobo,
                       transfer_id=str(transfer_id))
            return m

    # -- repayment ---------------------------------------------------------------------
    def apply_payment(
        self, tenant_state_id: str, mortgage_id: str,
        amount_kobo: int, idempotency_key: str, allow_credit: bool = False,
    ) -> Payment:
        if amount_kobo <= 0:
            raise ValueError("amount_kobo must be positive")
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            dedupe_key = f"{mortgage_id}|{idempotency_key}"
            existing = state.payment_keys.get(dedupe_key)
            if existing is not None:
                if existing.amount_kobo != amount_kobo:
                    raise ConflictError(
                        f"idempotency_key {idempotency_key!r} already used with "
                        f"amount {existing.amount_kobo}")
                return existing  # replay: same key + same amount → same result
            if m.status not in (MortgageStatus.DISBURSED, MortgageStatus.ACTIVE):
                raise InvalidTransitionError(
                    f"payments require DISBURSED/ACTIVE, got {m.status.value}")

            # Overpayment guard: never silently discard excess. Reject
            # amount > total outstanding unless the caller explicitly accepts
            # a borrower credit (allow_credit=true).
            total_outstanding = sum(i.outstanding_kobo for i in m.schedule)
            if amount_kobo > total_outstanding and not allow_credit:
                raise ValueError(
                    f"amount_kobo {amount_kobo} exceeds total outstanding "
                    f"{total_outstanding}; pass allow_credit=true to route the "
                    f"excess to the borrower credit account")

            # ledger first (two-phase): borrower → treasury; no post → no books
            transfer_id = deterministic_transfer_id(idempotency_key, "payment")
            borrower = self.applicant_account(m.applicant_id)
            treasury = self.treasury_account(state.tenant_state_id)
            self.ledger.hold(transfer_id, borrower, treasury, amount_kobo, idempotency_key)
            try:
                self.ledger.post(transfer_id)
            except Exception:
                self.ledger.void(transfer_id)
                raise

            # allocate oldest-first: interest then principal per installment
            remaining = amount_kobo
            interest_paid = 0
            principal_paid = 0
            for inst in m.schedule:
                if remaining == 0:
                    break
                interest_due = inst.interest_kobo - inst.paid_interest_kobo
                take = min(remaining, interest_due)
                inst.paid_interest_kobo += take
                interest_paid += take
                remaining -= take
                principal_due = inst.principal_kobo - inst.paid_principal_kobo
                take = min(remaining, principal_due)
                inst.paid_principal_kobo += take
                principal_paid += take
                remaining -= take
            # overpayment reduces outstanding principal directly
            if remaining > 0:
                prepay = min(remaining, m.outstanding_principal_kobo - principal_paid)
                principal_paid += prepay
                remaining -= prepay
            m.outstanding_principal_kobo -= principal_paid
            # any true excess is routed to the borrower credit account
            # (allow_credit path only) — never silently discarded
            overpayment = remaining
            if overpayment > 0:
                m.credit_balance_kobo += overpayment

            payment = Payment(
                payment_id=_id("pay"),
                idempotency_key=idempotency_key,
                amount_kobo=amount_kobo,
                interest_kobo=interest_paid,
                principal_kobo=principal_paid,
                overpayment_kobo=overpayment,
                transfer_id=str(transfer_id),
                applied_at=_iso(self._now()),
            )
            m.payments.append(payment)
            state.payment_keys[dedupe_key] = payment
            m.status = MortgageStatus.ACTIVE
            self._audit(state, m.mortgage_id, "payment_applied",
                        payment_id=payment.payment_id, amount_kobo=amount_kobo,
                        interest_kobo=interest_paid, principal_kobo=principal_paid,
                        overpayment_kobo=overpayment,
                        credit_balance_kobo=m.credit_balance_kobo,
                        outstanding_principal_kobo=m.outstanding_principal_kobo)
            self._emit(ev.EVENT_PAYMENT_APPLIED, f"{m.mortgage_id}|payment_applied|{payment.payment_id}",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=payment.applied_at, payment_id=payment.payment_id,
                       amount_kobo=amount_kobo, interest_kobo=interest_paid,
                       principal_kobo=principal_paid, overpayment_kobo=overpayment,
                       outstanding_principal_kobo=m.outstanding_principal_kobo)

            if m.outstanding_principal_kobo == 0 and all(i.settled for i in m.schedule):
                self._discharge_locked(state, m)
            return payment

    def borrower_credit_account(self, applicant_id: str) -> int:
        return deterministic_account_id("credit", applicant_id)

    def _discharge_locked(self, state: TenantState, m: Mortgage) -> None:
        """Discharge the mortgage and release the lien atomically (caller
        holds the lock; the ledger post for the final payment has already
        succeeded — a failed post never reaches this point).

        Any borrower credit balance (accepted overpayments) is refunded with
        a dedicated treasury → borrower-credit ledger leg BEFORE discharge;
        if the refund leg fails the discharge does not happen."""
        refund_id = ""
        if m.credit_balance_kobo > 0 and m.credit_refund_transfer_id is None:
            key = f"{m.mortgage_id}|discharge-credit-refund"
            refund_tid = deterministic_transfer_id(key, "credit_refund")
            treasury = self.treasury_account(state.tenant_state_id)
            credit_acct = self.borrower_credit_account(m.applicant_id)
            self.ledger.hold(refund_tid, treasury, credit_acct,
                             m.credit_balance_kobo, key)
            try:
                self.ledger.post(refund_tid)
            except Exception:
                self.ledger.void(refund_tid)
                raise
            m.credit_refund_transfer_id = str(refund_tid)
            refund_id = str(refund_tid)
            self._audit(state, m.mortgage_id, "credit_refunded",
                        amount_kobo=m.credit_balance_kobo,
                        transfer_id=refund_id)
            m.credit_balance_kobo = 0
        m.status = MortgageStatus.DISCHARGED
        m.discharged_at = _iso(self._now())
        lien = state.liens.get(m.lien_id or "")
        lien_id = ""
        if lien is not None and lien.status is LienStatus.REGISTERED:
            lien.status = LienStatus.RELEASED
            lien.released_at = m.discharged_at
            lien_id = lien.lien_id
        self._audit(state, m.mortgage_id, "discharged", lien_id=lien_id,
                    to=m.status.value)
        self._emit(ev.EVENT_DISCHARGED, f"{m.mortgage_id}|discharged",
                   tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                   occurred_at=m.discharged_at, lien_id=lien_id,
                   credit_refund_transfer_id=refund_id)

    # -- default & foreclosure -----------------------------------------------------------
    def _evaluate_default(self, state: TenantState, m: Mortgage) -> Mortgage:
        if m.status in (MortgageStatus.DISBURSED, MortgageStatus.ACTIVE):
            dpd = m.days_past_due(self._now())
            if dpd > DEFAULT_DAYS_PAST_DUE:
                m.status = MortgageStatus.DEFAULTED
                m.defaulted_at = _iso(self._now())
                self._audit(state, m.mortgage_id, "defaulted",
                            days_past_due=dpd, to=m.status.value)
                self._emit(ev.EVENT_DEFAULTED, f"{m.mortgage_id}|defaulted",
                           tenant_state_id=state.tenant_state_id,
                           mortgage_id=m.mortgage_id,
                           occurred_at=m.defaulted_at, days_past_due=dpd)
        return m

    def evaluate_default(self, tenant_state_id: str, mortgage_id: str) -> Mortgage:
        state = self.tenant(tenant_state_id)
        with self._lock:
            return self._evaluate_default(state, self._get(state, mortgage_id))

    def foreclose(self, tenant_state_id: str, mortgage_id: str, reason: str) -> Mortgage:
        if not reason:
            raise ValueError("foreclosure requires a reason")
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            self._evaluate_default(state, m)
            if m.status is not MortgageStatus.DEFAULTED:
                raise InvalidTransitionError(
                    f"foreclosure requires DEFAULTED, got {m.status.value}")
            m.status = MortgageStatus.FORECLOSED
            m.foreclosed_at = _iso(self._now())
            m.foreclosure_reason = reason
            self._audit(state, m.mortgage_id, "foreclosed", reason=reason,
                        to=m.status.value)
            self._emit(ev.EVENT_FORECLOSED, f"{m.mortgage_id}|foreclosed",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.foreclosed_at, reason=reason)
            return m

    # -- foreclosure pipeline: FORECLOSED → POSSESSION_REGISTERED →
    #    SALE_AUTHORIZED → SOLD → PROCEEDS_DISTRIBUTED → CLOSED ----------------
    def sale_escrow_account(self, mortgage_id: str) -> int:
        return deterministic_account_id("sale_escrow", mortgage_id)

    def purchaser_account(self, purchaser_id: str) -> int:
        return deterministic_account_id("purchaser", purchaser_id)

    def sale_costs_account(self, mortgage_id: str) -> int:
        return deterministic_account_id("sale_costs", mortgage_id)

    def register_possession(
        self, tenant_state_id: str, mortgage_id: str, reference: str
    ) -> Mortgage:
        """FORECLOSED → POSSESSION_REGISTERED (court/consent reference required)."""
        if not reference:
            raise ValueError("possession registration requires a court/consent reference")
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.FORECLOSED:
                raise InvalidTransitionError(
                    f"possession registration requires FORECLOSED, got {m.status.value}")
            m.status = MortgageStatus.POSSESSION_REGISTERED
            m.possession_reference = reference
            m.possession_registered_at = _iso(self._now())
            self._audit(state, m.mortgage_id, "possession_registered",
                        reference=reference, to=m.status.value)
            self._emit(ev.EVENT_POSSESSION_REGISTERED,
                       f"{m.mortgage_id}|possession_registered",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.possession_registered_at, reference=reference)
            return m

    def authorize_sale(
        self, tenant_state_id: str, mortgage_id: str,
        valuation_kobo: int, reserve_price_kobo: int, valuer_id: str,
    ) -> Mortgage:
        """POSSESSION_REGISTERED → SALE_AUTHORIZED (independent valuation)."""
        if valuation_kobo <= 0 or reserve_price_kobo <= 0:
            raise ValueError("valuation_kobo and reserve_price_kobo must be positive")
        if reserve_price_kobo > valuation_kobo:
            raise ValueError("reserve_price_kobo must not exceed valuation_kobo")
        if not valuer_id:
            raise ValueError("sale authorization requires an independent valuer_id")
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.POSSESSION_REGISTERED:
                raise InvalidTransitionError(
                    f"sale authorization requires POSSESSION_REGISTERED, got {m.status.value}")
            m.status = MortgageStatus.SALE_AUTHORIZED
            m.valuation_kobo = valuation_kobo
            m.reserve_price_kobo = reserve_price_kobo
            m.valuer_id = valuer_id
            m.sale_authorized_at = _iso(self._now())
            self._audit(state, m.mortgage_id, "sale_authorized",
                        valuation_kobo=valuation_kobo,
                        reserve_price_kobo=reserve_price_kobo,
                        valuer_id=valuer_id, to=m.status.value)
            self._emit(ev.EVENT_SALE_AUTHORIZED, f"{m.mortgage_id}|sale_authorized",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.sale_authorized_at, valuation_kobo=valuation_kobo,
                       reserve_price_kobo=reserve_price_kobo, valuer_id=valuer_id)
            return m

    def sell(
        self, tenant_state_id: str, mortgage_id: str,
        purchaser_id: str, gross_proceeds_kobo: int, sale_costs_kobo: int = 0,
    ) -> Mortgage:
        """SALE_AUTHORIZED → SOLD.

        * gross proceeds must meet the reserve price;
        * proceeds move purchaser → sale escrow via two-phase ledger
          (PENDING hold → POST, VOID on failure);
        * the title is transferred to the purchaser through the fail-closed
          :class:`LandTitleTransferAdapter` seam — if the title transfer
          fails, the sale is NOT recorded (saga: proceeds transfer voided).
        """
        if not purchaser_id:
            raise ValueError("sale requires a purchaser_id")
        if gross_proceeds_kobo <= 0:
            raise ValueError("gross_proceeds_kobo must be positive")
        if sale_costs_kobo < 0:
            raise ValueError("sale_costs_kobo must not be negative")
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.SALE_AUTHORIZED:
                raise InvalidTransitionError(
                    f"sale requires SALE_AUTHORIZED, got {m.status.value}")
            if gross_proceeds_kobo < (m.reserve_price_kobo or 0):
                raise ValueError(
                    f"gross_proceeds_kobo {gross_proceeds_kobo} below reserve "
                    f"price {m.reserve_price_kobo}")
            key = f"{m.mortgage_id}|foreclosure-sale"
            transfer_id = deterministic_transfer_id(key, "sale_proceeds")
            escrow = self.sale_escrow_account(m.mortgage_id)
            purchaser = self.purchaser_account(purchaser_id)
            self.ledger.hold(transfer_id, purchaser, escrow, gross_proceeds_kobo, key)
            try:
                self.ledger.post(transfer_id)
                # title transfer is part of the sale saga: no title, no sale
                transfer_ref = self.title_transfer.transfer_title(
                    state.tenant_state_id, m.parcel_id, m.applicant_id,
                    purchaser_id, reference=m.possession_reference or m.mortgage_id)
            except Exception:
                try:
                    self.ledger.void(transfer_id)
                except Exception:
                    pass  # posted legs are compensated via reversal flow
                raise
            m.status = MortgageStatus.SOLD
            m.purchaser_id = purchaser_id
            m.gross_proceeds_kobo = gross_proceeds_kobo
            m.sale_costs_kobo = sale_costs_kobo
            m.title_transfer_ref = transfer_ref
            m.sale_transfer_id = str(transfer_id)
            m.sold_at = _iso(self._now())
            self._audit(state, m.mortgage_id, "sold",
                        purchaser_id=purchaser_id,
                        gross_proceeds_kobo=gross_proceeds_kobo,
                        sale_costs_kobo=sale_costs_kobo,
                        title_transfer_ref=transfer_ref,
                        transfer_id=str(transfer_id), to=m.status.value)
            self._emit(ev.EVENT_SOLD, f"{m.mortgage_id}|sold",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.sold_at, purchaser_id=purchaser_id,
                       gross_proceeds_kobo=gross_proceeds_kobo,
                       sale_costs_kobo=sale_costs_kobo,
                       title_transfer_ref=transfer_ref,
                       transfer_id=str(transfer_id))
            return m

    def _outstanding_total(self, m: Mortgage) -> int:
        return sum(i.outstanding_kobo for i in m.schedule)

    def distribute_proceeds(self, tenant_state_id: str, mortgage_id: str) -> Mortgage:
        """SOLD → PROCEEDS_DISTRIBUTED → CLOSED (atomic waterfall).

        Deterministic integer-kobo waterfall from the sale escrow:
        (1) sale costs, (2) senior lien outstanding, (3) junior lien
        outstanding, (4) borrower surplus. If proceeds are insufficient the
        shortfall is recorded as ``deficiency_kobo`` on the borrower. Every
        leg is a deterministic-id two-phase transfer; a failure voids the
        whole saga (no partial distribution)."""
        state = self.tenant(tenant_state_id)
        with self._lock:
            m = self._get(state, mortgage_id)
            if m.status is not MortgageStatus.SOLD:
                raise InvalidTransitionError(
                    f"proceeds distribution requires SOLD, got {m.status.value}")
            gross = m.gross_proceeds_kobo or 0
            key = f"{m.mortgage_id}|foreclosure-distribution"
            escrow = self.sale_escrow_account(m.mortgage_id)
            treasury = self.treasury_account(state.tenant_state_id)
            borrower = self.applicant_account(m.applicant_id)

            # lien waterfall: this mortgage is senior unless its lien is a
            # second charge; find the junior lien's mortgage on the parcel.
            my_lien = state.liens.get(m.lien_id or "")
            senior_due = self._outstanding_total(m)
            junior_due = 0
            junior_m: Optional[Mortgage] = None
            if my_lien is not None and my_lien.priority == 1:
                for other in state.liens.values():
                    if other.parcel_id == m.parcel_id and other.priority == 2 \
                            and other.status is LienStatus.REGISTERED \
                            and other.mortgage_id != m.mortgage_id:
                        junior_m = state.mortgages.get(other.mortgage_id)
                        if junior_m is not None:
                            junior_due = self._outstanding_total(junior_m)
                        break

            costs_due = m.sale_costs_kobo or 0
            legs: List[tuple] = []  # (leg, amount, credit_account)
            remaining = gross
            costs_paid = min(costs_due, remaining)
            legs.append(("costs", costs_paid, self.sale_costs_account(m.mortgage_id)))
            remaining -= costs_paid
            senior_paid = min(senior_due, remaining)
            legs.append(("senior", senior_paid, treasury))
            remaining -= senior_paid
            junior_paid = min(junior_due, remaining)
            if junior_paid:
                legs.append(("junior", junior_paid, treasury))
                remaining -= junior_paid
            surplus = remaining
            if surplus:
                legs.append(("surplus", surplus, borrower))
            deficiency = max(0, costs_due + senior_due + junior_due - gross)

            # two-phase saga: hold all legs, then post all; on failure void
            # every leg (best effort — posted legs are compensated by the
            # ledger's reversal flow in production)
            leg_ids: List[int] = []
            try:
                for leg, amount, credit_acct in legs:
                    if amount <= 0:
                        continue
                    tid = deterministic_transfer_id(key, leg)
                    leg_ids.append(tid)
                    self.ledger.hold(tid, escrow, credit_acct, amount, key)
                for tid in leg_ids:
                    self.ledger.post(tid)
            except Exception:
                for tid in reversed(leg_ids):
                    try:
                        self.ledger.void(tid)
                    except Exception:
                        pass
                raise

            # apply senior proceeds to the schedule (oldest-first)
            alloc = senior_paid
            for inst in m.schedule:
                if alloc == 0:
                    break
                interest_due = inst.interest_kobo - inst.paid_interest_kobo
                take = min(alloc, interest_due)
                inst.paid_interest_kobo += take
                alloc -= take
                principal_due = inst.principal_kobo - inst.paid_principal_kobo
                take = min(alloc, principal_due)
                inst.paid_principal_kobo += take
                alloc -= take
            m.outstanding_principal_kobo -= min(
                senior_paid, m.outstanding_principal_kobo)
            if junior_m is not None and junior_paid:
                alloc = junior_paid
                for inst in junior_m.schedule:
                    if alloc == 0:
                        break
                    interest_due = inst.interest_kobo - inst.paid_interest_kobo
                    take = min(alloc, interest_due)
                    inst.paid_interest_kobo += take
                    alloc -= take
                    principal_due = inst.principal_kobo - inst.paid_principal_kobo
                    take = min(alloc, principal_due)
                    inst.paid_principal_kobo += take
                    alloc -= take
                junior_m.outstanding_principal_kobo -= min(
                    junior_paid, junior_m.outstanding_principal_kobo)

            m.distribution = {
                "gross_proceeds_kobo": gross,
                "costs_kobo": costs_paid,
                "senior_kobo": senior_paid,
                "junior_kobo": junior_paid,
                "surplus_kobo": surplus,
                "deficiency_kobo": deficiency,
                "transfer_ids": {
                    leg: str(deterministic_transfer_id(key, leg))
                    for leg, amount, _ in legs if amount > 0
                },
            }
            m.deficiency_kobo = deficiency
            m.proceeds_distributed_at = _iso(self._now())
            m.status = MortgageStatus.PROCEEDS_DISTRIBUTED
            self._audit(state, m.mortgage_id, "proceeds_distributed",
                        **{k: m.distribution[k] for k in
                           ("gross_proceeds_kobo", "costs_kobo", "senior_kobo",
                            "junior_kobo", "surplus_kobo", "deficiency_kobo")},
                        to=m.status.value)
            self._emit(ev.EVENT_PROCEEDS_DISTRIBUTED,
                       f"{m.mortgage_id}|proceeds_distributed",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.proceeds_distributed_at,
                       gross_proceeds_kobo=gross, costs_kobo=costs_paid,
                       senior_kobo=senior_paid, junior_kobo=junior_paid,
                       surplus_kobo=surplus, deficiency_kobo=deficiency)

            # CLOSED: release every lien on the parcel, atomically
            released: List[str] = []
            for lien in state.liens.values():
                if lien.parcel_id == m.parcel_id and lien.status is LienStatus.REGISTERED:
                    lien.status = LienStatus.RELEASED
                    lien.released_at = m.proceeds_distributed_at
                    released.append(lien.lien_id)
            m.status = MortgageStatus.CLOSED
            m.closed_at = m.proceeds_distributed_at
            self._audit(state, m.mortgage_id, "closed",
                        lien_ids=",".join(released), to=m.status.value)
            self._emit(ev.EVENT_CLOSED, f"{m.mortgage_id}|closed",
                       tenant_state_id=state.tenant_state_id, mortgage_id=m.mortgage_id,
                       occurred_at=m.closed_at, lien_ids=",".join(released))
            return m
