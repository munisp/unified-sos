"""Revocation for public purpose + compensation (Land Use Act s.28).

``RevocationWorkflow``::

    NOTICE_ISSUED → PUBLIC_PURPOSE_VERIFIED → COMPENSATION_ASSESSED
    → GOVERNOR_INSTRUMENT_SIGNED → REVOCATION_EFFECTIVE

* COMPENSATION_ASSESSED records line items (LAND / IMPROVEMENTS / CROPS /
  DISTURBANCE) in integer **kobo**, the assessing valuer, and an optional
  override reason. A two-phase hold is placed on the compensation ledger via
  the fail-closed :class:`LandCompensationLedgerAdapter` seam
  (``SOS_LANDS_LEDGER_URL``; fixture default). Hold ids are deterministic:
  ``{revocation_id}|{claimant}|{version}``.
* GOVERNOR_INSTRUMENT_SIGNED mints an Ed25519-signed revocation instrument
  chained to the parcel's original title hash. The parcel is only marked
  ``REVOKED`` after this instrument is signed.
* REVOCATION_EFFECTIVE posts the held payout (``COMPENSATION_PAID``); if
  posting fails the hold is **voided** (conservation: no partial payouts).

Events: ``TITLE_REVOKED``, ``COMPENSATION_PAID``.
"""

from __future__ import annotations

import enum
import hashlib
import itertools
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Protocol
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import signing
from .eventlog import CadastreEventLog
from .repository import ParcelRepository
from .schemas import ParcelStatus
from .risk import AdapterUnavailableError
from .transfers import (
    TitleRecord,
    TitleRegistry,
    TitleStatus,
    TransferNotFoundError,
)

# NOTE: no DEFAULT_LEDGER_URL — the compensation ledger is an
# external-system seam (no in-cluster finance-ledger service ships with the
# platform), so production requires an explicit ``SOS_LANDS_LEDGER_URL``
# (fail-closed); the fixture ledger remains the dev/test default.


class RevocationStage(str, enum.Enum):
    NOTICE_ISSUED = "NOTICE_ISSUED"
    PUBLIC_PURPOSE_VERIFIED = "PUBLIC_PURPOSE_VERIFIED"
    COMPENSATION_ASSESSED = "COMPENSATION_ASSESSED"
    GOVERNOR_INSTRUMENT_SIGNED = "GOVERNOR_INSTRUMENT_SIGNED"
    REVOCATION_EFFECTIVE = "REVOCATION_EFFECTIVE"


REVOCATION_STAGE_ORDER: tuple[RevocationStage, ...] = (
    RevocationStage.NOTICE_ISSUED,
    RevocationStage.PUBLIC_PURPOSE_VERIFIED,
    RevocationStage.COMPENSATION_ASSESSED,
    RevocationStage.GOVERNOR_INSTRUMENT_SIGNED,
    RevocationStage.REVOCATION_EFFECTIVE,
)


class CompensationCategory(str, enum.Enum):
    LAND = "LAND"
    IMPROVEMENTS = "IMPROVEMENTS"
    CROPS = "CROPS"
    DISTURBANCE = "DISTURBANCE"


class RevocationError(Exception):
    """Illegal revocation operation (mapped to HTTP 409)."""


# ---------------------------------------------------------------------------
# Compensation ledger seam (fail-closed; two-phase hold/post/void)
# ---------------------------------------------------------------------------


class LandCompensationLedgerAdapter(Protocol):
    """Adapter seam: two-phase compensation payouts (hold → post | void)."""

    def hold(
        self, *, hold_id: str, tenant_state_id: str, claimant: str, amount_kobo: int
    ) -> str: ...

    def post(self, *, hold_id: str) -> None: ...

    def void(self, *, hold_id: str) -> None: ...


class FixtureCompensationLedger:
    """Deterministic fixture ledger (default; local dev and tests).

    Conservation: every hold is either posted (moved to ``posted``) or voided
    (moved to ``voided``); a hold id can only be settled once, so no payout is
    ever duplicated or lost.
    """

    name = "fixture"

    def __init__(self) -> None:
        self.holds: Dict[str, dict] = {}
        self.posted: Dict[str, dict] = {}
        self.voided: Dict[str, dict] = {}

    def hold(
        self, *, hold_id: str, tenant_state_id: str, claimant: str, amount_kobo: int
    ) -> str:
        if hold_id in self.holds or hold_id in self.posted:
            raise RevocationError(f"duplicate ledger hold id {hold_id!r}")
        entry = {
            "hold_id": hold_id,
            "tenant_state_id": tenant_state_id,
            "claimant": claimant,
            "amount_kobo": amount_kobo,
        }
        self.holds[hold_id] = entry
        return hold_id

    def post(self, *, hold_id: str) -> None:
        entry = self.holds.pop(hold_id, None)
        if entry is None:
            raise RevocationError(f"cannot post hold {hold_id!r}: not held")
        self.posted[hold_id] = entry

    def void(self, *, hold_id: str) -> None:
        entry = self.holds.pop(hold_id, None)
        if entry is None:
            raise RevocationError(f"cannot void hold {hold_id!r}: not held")
        self.voided[hold_id] = entry


class HttpCompensationLedger:
    """Production ledger adapter — POSTs hold/post/void to the finance ledger.

    Fail-closed: any failure raises :class:`AdapterUnavailableError` rather
    than skipping or fabricating a compensation payout.
    """

    name = "http"

    def __init__(
        self,
        base_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_s: float = 5.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.base_url = base_url or env.get("SOS_LANDS_LEDGER_URL")
        if not self.base_url:
            raise AdapterUnavailableError(
                "SOS_LANDS_LEDGER_URL is required for HttpCompensationLedger "
                "(external-system seam; fail-closed: refusing to boot with "
                "an unconfigured compensation-ledger endpoint)"
            )
        self.timeout_s = timeout_s

    def _call(self, path: str, payload: dict) -> None:  # pragma: no cover
        try:
            import httpx
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpCompensationLedger"
            ) from exc
        try:
            resp = httpx.post(
                f"{self.base_url}{path}", json=payload, timeout=self.timeout_s
            )
            resp.raise_for_status()
        except Exception as exc:
            raise AdapterUnavailableError(
                f"compensation ledger unavailable at {self.base_url}: {exc} "
                "(fail-closed: refusing to fabricate a compensation payout)"
            ) from exc

    def hold(
        self, *, hold_id: str, tenant_state_id: str, claimant: str, amount_kobo: int
    ) -> str:  # pragma: no cover - requires live ledger
        self._call("/hold", {
            "hold_id": hold_id, "tenant_state_id": tenant_state_id,
            "claimant": claimant, "amount_kobo": amount_kobo,
        })
        return hold_id

    def post(self, *, hold_id: str) -> None:  # pragma: no cover
        self._call("/post", {"hold_id": hold_id})

    def void(self, *, hold_id: str) -> None:  # pragma: no cover
        self._call("/void", {"hold_id": hold_id})


def ledger_adapter_from_env(
    environ: Optional[Dict[str, str]] = None,
) -> LandCompensationLedgerAdapter:
    """Build the compensation ledger adapter from environment (fail-closed prod)."""
    env = environ if environ is not None else dict(os.environ)
    profile = env.get("SOS_LANDS_PROFILE", "dev")
    url = env.get("SOS_LANDS_LEDGER_URL")
    if profile == "production":
        if not url:
            raise AdapterUnavailableError(
                "SOS_LANDS_LEDGER_URL is required when SOS_LANDS_PROFILE=production "
                "(fail-closed: refusing to boot with the fixture ledger)"
            )
        return HttpCompensationLedger(url)
    if url:
        return HttpCompensationLedger(url)
    return FixtureCompensationLedger()


# ---------------------------------------------------------------------------
# Revocation workflow + service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompensationLineItem:
    """One assessed compensation line (integer kobo — never float money)."""

    category: CompensationCategory
    amount_kobo: int


@dataclass
class RevocationCase:
    """State of one public-purpose revocation."""

    revocation_id: str
    tenant_state_id: str
    parcel_id: UUID
    public_purpose: str
    claimant: str
    stage: RevocationStage
    created_at: datetime
    history: list[dict] = field(default_factory=list)
    line_items: list[CompensationLineItem] = field(default_factory=list)
    valuer: Optional[str] = None
    override_reason: Optional[str] = None
    hold_id: Optional[str] = None
    instrument_jws: Optional[str] = None
    original_title_hash: Optional[str] = None

    @property
    def total_kobo(self) -> int:
        return sum(item.amount_kobo for item in self.line_items)

    def as_dict(self) -> dict:
        return {
            "revocation_id": self.revocation_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "public_purpose": self.public_purpose,
            "claimant": self.claimant,
            "stage": self.stage.value,
            "line_items": [
                {"category": i.category.value, "amount_kobo": i.amount_kobo}
                for i in self.line_items
            ],
            "total_kobo": self.total_kobo,
            "valuer": self.valuer,
            "override_reason": self.override_reason,
            "hold_id": self.hold_id,
            "instrument_jws": self.instrument_jws,
            "original_title_hash": self.original_title_hash,
            "history": list(self.history),
            "created_at": self.created_at.isoformat(),
        }


class RevocationWorkflow:
    """Pure stage-transition rules for revocation (Temporal-ready, no I/O)."""

    @classmethod
    def advance(cls, case: RevocationCase, *, actor: str, note: str = "") -> RevocationStage:
        idx = REVOCATION_STAGE_ORDER.index(case.stage)
        if idx >= len(REVOCATION_STAGE_ORDER) - 1:
            raise RevocationError(
                f"revocation {case.revocation_id} is already REVOCATION_EFFECTIVE"
            )
        nxt = REVOCATION_STAGE_ORDER[idx + 1]
        case.stage = nxt
        case.history.append(
            {"stage": nxt.value, "actor": actor, "note": note,
             "at": datetime.now(timezone.utc).isoformat()}
        )
        return nxt


class RevocationService:
    """Orchestrates revocations against repo + ledger + signing + titles."""

    def __init__(
        self,
        repository: ParcelRepository,
        event_log: CadastreEventLog,
        ledger: LandCompensationLedgerAdapter,
        titles: TitleRegistry,
        governor_key: Ed25519PrivateKey,
        clock: Callable[[], datetime] | None = None,
        previous_title_hash: Callable[[str, UUID], str] | None = None,
    ) -> None:
        self._repo = repository
        self._log = event_log
        self._ledger = ledger
        self._titles = titles
        self._governor_key = governor_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._previous_title_hash = previous_title_hash or self._default_prev_hash
        self._cases: dict[str, dict[str, RevocationCase]] = {}
        self._seq = itertools.count(1)

    @staticmethod
    def _default_prev_hash(tenant_state_id: str, parcel_id: UUID) -> str:
        return hashlib.sha256(
            f"genesis|{tenant_state_id}|{parcel_id}".encode("utf-8")
        ).hexdigest()

    def get(self, tenant_state_id: str, revocation_id: str) -> RevocationCase:
        try:
            return self._cases.setdefault(tenant_state_id, {})[revocation_id]
        except KeyError:
            raise TransferNotFoundError(f"unknown revocation {revocation_id!r}") from None

    # -- NOTICE_ISSUED ------------------------------------------------------------
    def issue_notice(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        public_purpose: str,
        actor: str,
    ) -> RevocationCase:
        """Serve the revocation notice (Land Use Act s.28/44)."""
        record = self._repo.get(tenant_state_id, parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        if record.status not in (ParcelStatus.ACTIVE, ParcelStatus.REGISTERED):
            raise RevocationError(
                f"parcel {parcel_id} has status {record.status.value}; only "
                "ACTIVE/REGISTERED parcels can be revoked"
            )
        case = RevocationCase(
            revocation_id=f"revocation-{tenant_state_id}-{next(self._seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            public_purpose=public_purpose,
            claimant=record.owner_stin,
            stage=RevocationStage.NOTICE_ISSUED,
            created_at=self._clock(),
        )
        case.history.append(
            {"stage": RevocationStage.NOTICE_ISSUED.value, "actor": actor,
             "note": public_purpose, "at": self._clock().isoformat()}
        )
        self._cases.setdefault(tenant_state_id, {})[case.revocation_id] = case
        self._log.record(
            "REVOCATION_NOTICE_ISSUED",
            tenant_state_id,
            parcel_id=parcel_id,
            actor=actor,
            detail={"revocation_id": case.revocation_id,
                    "public_purpose": public_purpose},
        )
        return case

    # -- signal handler -------------------------------------------------------------
    def advance(
        self,
        tenant_state_id: str,
        revocation_id: str,
        *,
        actor: str,
        line_items: Optional[list[CompensationLineItem]] = None,
        valuer: Optional[str] = None,
        override_reason: Optional[str] = None,
        note: str = "",
    ) -> RevocationCase:
        """Advance one stage, running its activity (fail-closed)."""
        case = self.get(tenant_state_id, revocation_id)
        current = case.stage

        if current == RevocationStage.COMPENSATION_ASSESSED:
            # Activity: sign the governor's revocation instrument (Ed25519),
            # chained to the parcel's original title hash.
            self._sign_instrument(case)
            RevocationWorkflow.advance(case, actor=actor, note=note)
        elif current == RevocationStage.GOVERNOR_INSTRUMENT_SIGNED:
            # Activity: post the held payout, flip the parcel to REVOKED.
            self._effect_revocation(case, actor=actor)
            RevocationWorkflow.advance(case, actor=actor, note=note or "effective")
        elif current == RevocationStage.PUBLIC_PURPOSE_VERIFIED:
            # Activity: record the compensation assessment + ledger hold.
            self._assess_compensation(
                case, line_items=line_items or [], valuer=valuer,
                override_reason=override_reason,
            )
            RevocationWorkflow.advance(case, actor=actor, note=note)
        else:
            RevocationWorkflow.advance(case, actor=actor, note=note)
        return case

    def _assess_compensation(
        self,
        case: RevocationCase,
        *,
        line_items: list[CompensationLineItem],
        valuer: Optional[str],
        override_reason: Optional[str],
    ) -> None:
        if not line_items:
            raise RevocationError("compensation assessment requires line items")
        for item in line_items:
            if not isinstance(item.amount_kobo, int) or item.amount_kobo < 0:
                raise RevocationError(
                    "compensation amounts must be non-negative integer kobo"
                )
        if not valuer:
            raise RevocationError("compensation assessment requires a valuer")
        case.line_items = list(line_items)
        case.valuer = valuer
        case.override_reason = override_reason
        # Two-phase hold with a deterministic id: revocation_id|claimant|version.
        version = sum(
            1 for c in self._cases.setdefault(case.tenant_state_id, {}).values()
            if c.parcel_id == case.parcel_id
        )
        hold_id = f"{case.revocation_id}|{case.claimant}|{version}"
        self._ledger.hold(
            hold_id=hold_id,
            tenant_state_id=case.tenant_state_id,
            claimant=case.claimant,
            amount_kobo=case.total_kobo,
        )
        case.hold_id = hold_id
        self._log.record(
            "COMPENSATION_ASSESSED",
            case.tenant_state_id,
            parcel_id=case.parcel_id,
            actor=valuer,
            detail={"revocation_id": case.revocation_id,
                    "total_kobo": case.total_kobo, "valuer": valuer,
                    "override_reason": override_reason, "hold_id": hold_id},
        )

    def _sign_instrument(self, case: RevocationCase) -> None:
        record = self._repo.get(case.tenant_state_id, case.parcel_id)
        prev_hash = self._previous_title_hash(case.tenant_state_id, case.parcel_id)
        case.original_title_hash = prev_hash
        payload = {
            "doc_type": "REVOCATION_INSTRUMENT",
            "issuer": f"ng.sos.{case.tenant_state_id}.governor",
            "tenant_state_id": case.tenant_state_id,
            "parcel_id": str(case.parcel_id),
            "parcel_uin": record.parcel_uin if record else None,
            "revocation_id": case.revocation_id,
            "public_purpose": case.public_purpose,
            "claimant": case.claimant,
            "compensation_kobo": case.total_kobo,
            "original_title_sha256": prev_hash,
            "signed_at": self._clock().isoformat(),
        }
        case.instrument_jws = signing.sign_payload(payload, self._governor_key)
        self._log.record(
            "REVOCATION_INSTRUMENT_SIGNED",
            case.tenant_state_id,
            parcel_id=case.parcel_id,
            actor="governor",
            detail={"revocation_id": case.revocation_id,
                    "original_title_sha256": prev_hash},
        )

    def _effect_revocation(self, case: RevocationCase, *, actor: str) -> None:
        """Post the payout and mark the parcel REVOKED (only after signing)."""
        if case.instrument_jws is None:
            raise RevocationError(
                "parcel cannot be marked REVOKED before the governor instrument is signed"
            )
        assert case.hold_id is not None
        try:
            self._ledger.post(hold_id=case.hold_id)
        except Exception:
            # Conservation: a failed payout voids the hold — no partial state.
            self._ledger.void(hold_id=case.hold_id)
            raise
        record = self._repo.get(case.tenant_state_id, case.parcel_id)
        if record is not None:
            self._repo.update(record.model_copy(update={"status": ParcelStatus.REVOKED}))
            if record.c_of_o_number:
                if self._titles.find(case.tenant_state_id, record.c_of_o_number) is None:
                    # Titling-workflow titles enter the register here so their
                    # REVOKED status is visible to deed verification.
                    self._titles.register(
                        TitleRecord(
                            c_of_o_number=record.c_of_o_number,
                            tenant_state_id=case.tenant_state_id,
                            parcel_id=case.parcel_id,
                            owner_stin=record.owner_stin,
                            title_jws="",
                            instrument="E_C_OF_O",
                            status=TitleStatus.CURRENT,
                            issued_at=self._clock().isoformat(),
                        )
                    )
                self._titles.mark_revoked(
                    case.tenant_state_id, record.c_of_o_number,
                    revocation_reference=case.revocation_id,
                )
        self._log.record(
            "TITLE_REVOKED",
            case.tenant_state_id,
            parcel_id=case.parcel_id,
            actor=actor,
            detail={"revocation_id": case.revocation_id,
                    "public_purpose": case.public_purpose},
        )
        self._log.record(
            "COMPENSATION_PAID",
            case.tenant_state_id,
            parcel_id=case.parcel_id,
            actor=actor,
            detail={"revocation_id": case.revocation_id,
                    "hold_id": case.hold_id,
                    "claimant": case.claimant,
                    "total_kobo": case.total_kobo},
        )
