"""Transfer of ownership — the critical conveyancing primitive.

``TransferWorkflow`` + ``TransferService`` implement a registered ownership
change on a parcel. Instrument types (dealings):

* ``SALE`` — sale/assignment for value (governor consent required),
* ``GIFT`` — voluntary transfer without consideration,
* ``ASSENT`` — probate/transmission assent by executors (see succession.py),
* ``COURT_ORDER`` — court-ordered vesting/rectification (see court_orders.py),
* ``FORECLOSURE_SALE`` — mortgagee foreclosure sale (consent required),
* ``PARTITION`` — partition among co-owners (consent required).

Stage topology::

    APPLICATION → EVIDENCE_VERIFICATION → CONSENT → TAX_CLEARANCE → REGISTERED

* EVIDENCE_VERIFICATION verifies the supporting document id against
  mod-land-docs via the fail-closed :class:`~lands_app.legal_adapters.LandDocsAdapter`
  seam (``SOS_LANDS_DOCS_URL``; fixture default).
* CONSENT consumes a governor consent instrument exactly once; the instrument
  carries ``expires_at`` and lapses after it. Only SALE-class dealings
  (``CONSENT_REQUIRED``) need one; other instruments pass the stage directly.
* TAX_CLEARANCE obtains a certificate via the fail-closed
  :class:`~lands_app.legal_adapters.TaxClearanceAdapter` seam
  (``SOS_LANDS_TAX_URL``; fixture default).
* REGISTERED runs the atomic registration activity: an Ed25519-signed
  transfer JWS (chained to the previous title hash, :mod:`lands_app.signing`),
  the parcel ownership row is updated, a new title is issued and the old
  title is marked REPLACED (never deleted), and ``TITLE_TRANSFERRED`` is
  appended to the hash-chained event log.

Guards (fail-closed, HTTP 409 at the API): any active encumbrance
(:class:`~lands_app.encumbrances.EncumbranceGuard`) or open dispute freezes
the dealing; the transferor must match the current registered owner.

Temporal-ready: like :mod:`lands_app.titling`, the workflow is code-defined —
:class:`TransferWorkflow` is pure orchestration over instance state while all
side effects live in :class:`TransferService` activity methods.
"""

from __future__ import annotations

import enum
import hashlib
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import signing
from .disputes import DisputeGuard
from .encumbrances import EncumbranceGuard
from .eventlog import CadastreEventLog
from .legal_adapters import LandDocsAdapter, TaxClearanceAdapter
from .repository import ParcelRepository
from .schemas import ParcelRecord, ParcelStatus


class TransferInstrumentType(str, enum.Enum):
    SALE = "SALE"
    GIFT = "GIFT"
    ASSENT = "ASSENT"
    COURT_ORDER = "COURT_ORDER"
    FORECLOSURE_SALE = "FORECLOSURE_SALE"
    PARTITION = "PARTITION"


class TransferStage(str, enum.Enum):
    APPLICATION = "APPLICATION"
    EVIDENCE_VERIFICATION = "EVIDENCE_VERIFICATION"
    CONSENT = "CONSENT"
    TAX_CLEARANCE = "TAX_CLEARANCE"
    REGISTERED = "REGISTERED"


#: Linear stage order (REGISTERED is terminal).
TRANSFER_STAGE_ORDER: tuple[TransferStage, ...] = (
    TransferStage.APPLICATION,
    TransferStage.EVIDENCE_VERIFICATION,
    TransferStage.CONSENT,
    TransferStage.TAX_CLEARANCE,
    TransferStage.REGISTERED,
)

#: SALE/ASSIGNMENT-class dealings that require a governor consent instrument.
CONSENT_REQUIRED: frozenset[TransferInstrumentType] = frozenset(
    {
        TransferInstrumentType.SALE,
        TransferInstrumentType.FORECLOSURE_SALE,
        TransferInstrumentType.PARTITION,
    }
)

#: Parcel statuses eligible for a dealing.
TRANSFERABLE_STATUSES = (ParcelStatus.ACTIVE, ParcelStatus.REGISTERED)


class TransferError(Exception):
    """Base error for transfer operations (mapped to HTTP 409)."""


class TransferNotFoundError(TransferError):
    """Unknown transfer/consent id (mapped to HTTP 404)."""


class EvidenceVerificationError(TransferError):
    """The evidence document failed mod-land-docs verification (409)."""


class ConsentError(TransferError):
    """Missing/expired/consumed/mismatched governor consent instrument (409)."""


# ---------------------------------------------------------------------------
# Title registry — current/replaced/revoked title chain per parcel
# ---------------------------------------------------------------------------


class TitleStatus(str, enum.Enum):
    CURRENT = "CURRENT"
    REPLACED = "REPLACED"  # superseded by a newer title; never deleted
    REVOKED = "REVOKED"


@dataclass
class TitleRecord:
    """One entry in the title register (audit-preserving chain of titles)."""

    c_of_o_number: str
    tenant_state_id: str
    parcel_id: UUID
    owner_stin: str
    title_jws: str
    instrument: str
    status: TitleStatus
    issued_at: str
    replaced_by: Optional[str] = None
    revocation_reference: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "c_of_o_number": self.c_of_o_number,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "owner_stin": self.owner_stin,
            "instrument": self.instrument,
            "status": self.status.value,
            "issued_at": self.issued_at,
            "replaced_by": self.replaced_by,
            "revocation_reference": self.revocation_reference,
        }


class TitleRegistry:
    """Tenant-scoped register of issued titles (never deletes — REPLACED/REVOKED)."""

    def __init__(self) -> None:
        # tenant_state_id -> c_of_o_number -> TitleRecord
        self._by_number: dict[str, dict[str, TitleRecord]] = {}

    def _tenant(self, tenant_state_id: str) -> dict[str, TitleRecord]:
        return self._by_number.setdefault(tenant_state_id, {})

    def register(self, record: TitleRecord) -> None:
        self._tenant(record.tenant_state_id)[record.c_of_o_number] = record

    def find(self, tenant_state_id: str, c_of_o_number: str) -> Optional[TitleRecord]:
        return self._tenant(tenant_state_id).get(c_of_o_number)

    def current_for(self, tenant_state_id: str, parcel_id: UUID) -> Optional[TitleRecord]:
        for record in self._tenant(tenant_state_id).values():
            if record.parcel_id == parcel_id and record.status == TitleStatus.CURRENT:
                return record
        return None

    def mark_replaced(
        self, tenant_state_id: str, c_of_o_number: str, *, replaced_by: str
    ) -> None:
        record = self.find(tenant_state_id, c_of_o_number)
        if record is not None and record.status == TitleStatus.CURRENT:
            record.status = TitleStatus.REPLACED
            record.replaced_by = replaced_by

    def mark_revoked(
        self, tenant_state_id: str, c_of_o_number: str, *, revocation_reference: str
    ) -> None:
        record = self.find(tenant_state_id, c_of_o_number)
        if record is not None and record.status != TitleStatus.REVOKED:
            record.status = TitleStatus.REVOKED
            record.revocation_reference = revocation_reference


# ---------------------------------------------------------------------------
# Governor consent instrument (single-use, expiring)
# ---------------------------------------------------------------------------


@dataclass
class ConsentInstrument:
    """A governor consent to a dealing; consumed exactly once, lapses at expiry."""

    consent_id: str
    tenant_state_id: str
    parcel_id: UUID
    expires_at: datetime
    issued_by: str
    issued_at: datetime
    consumed_by: Optional[str] = None  # transfer_id once consumed

    @property
    def consumed(self) -> bool:
        return self.consumed_by is not None

    def as_dict(self) -> dict:
        return {
            "consent_id": self.consent_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "expires_at": self.expires_at.isoformat(),
            "issued_by": self.issued_by,
            "issued_at": self.issued_at.isoformat(),
            "consumed": self.consumed,
            "consumed_by": self.consumed_by,
        }


# ---------------------------------------------------------------------------
# Transfer workflow instance + pure orchestration
# ---------------------------------------------------------------------------


@dataclass
class TransferApplication:
    """State of one transfer dealing (Temporal workflow execution equiv.)."""

    transfer_id: str
    tenant_state_id: str
    parcel_id: UUID
    instrument_type: TransferInstrumentType
    transferor_stin: str
    transferee_stin: str
    evidence_document_id: str
    stage: TransferStage
    created_at: datetime
    applicant: str
    history: list[dict] = field(default_factory=list)
    consent_id: Optional[str] = None
    tax_clearance_id: Optional[str] = None
    transfer_jws: Optional[str] = None
    new_c_of_o_number: Optional[str] = None
    previous_title_hash: Optional[str] = None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "transfer_id": self.transfer_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "instrument_type": self.instrument_type.value,
            "transferor_stin": self.transferor_stin,
            "transferee_stin": self.transferee_stin,
            "evidence_document_id": self.evidence_document_id,
            "stage": self.stage.value,
            "consent_id": self.consent_id,
            "tax_clearance_id": self.tax_clearance_id,
            "transfer_jws": self.transfer_jws,
            "new_c_of_o_number": self.new_c_of_o_number,
            "previous_title_hash": self.previous_title_hash,
            "detail": self.detail,
            "history": list(self.history),
            "created_at": self.created_at.isoformat(),
        }


class TransferWorkflow:
    """Pure stage-transition rules for a transfer dealing (no I/O)."""

    @staticmethod
    def next_stage(stage: TransferStage) -> TransferStage:
        idx = TRANSFER_STAGE_ORDER.index(stage)
        if idx >= len(TRANSFER_STAGE_ORDER) - 1:
            raise TransferError("transfer is already REGISTERED (terminal)")
        return TRANSFER_STAGE_ORDER[idx + 1]

    @classmethod
    def advance(cls, application: TransferApplication, *, actor: str, note: str = "") -> TransferStage:
        """Move the application to its next stage, recording the transition."""
        nxt = cls.next_stage(application.stage)
        application.stage = nxt
        application.history.append(
            {
                "stage": nxt.value,
                "actor": actor,
                "note": note,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return nxt


# ---------------------------------------------------------------------------
# Transfer service — activities + atomic registration
# ---------------------------------------------------------------------------


class TransferService:
    """Orchestrates transfer dealings against repo + adapters + guards.

    ``previous_title_hash`` is an injectable lookup producing the hash the
    signed transfer JWS chains to (main.py wires it to the title registry and
    the titling runner); the default hashes the parcel identity.
    """

    def __init__(
        self,
        repository: ParcelRepository,
        event_log: CadastreEventLog,
        encumbrance_guard: EncumbranceGuard,
        dispute_guard: DisputeGuard,
        docs_adapter: LandDocsAdapter,
        tax_adapter: TaxClearanceAdapter,
        titles: TitleRegistry,
        registry_key: Ed25519PrivateKey,
        clock: Callable[[], datetime] | None = None,
        sequence: Callable[[], int] | None = None,
        previous_title_hash: Callable[[str, UUID], str] | None = None,
    ) -> None:
        self._repo = repository
        self._log = event_log
        self._eguard = encumbrance_guard
        self._dguard = dispute_guard
        self._docs = docs_adapter
        self._tax = tax_adapter
        self._titles = titles
        self._registry_key = registry_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._seq = sequence or itertools.count(1).__next__
        self._previous_title_hash = previous_title_hash or self._default_prev_hash
        # tenant_state_id -> id -> object
        self._transfers: dict[str, dict[str, TransferApplication]] = {}
        self._consents: dict[str, dict[str, ConsentInstrument]] = {}
        self._id_seq = itertools.count(1)
        self._consent_seq = itertools.count(1)

    # -- lookups -------------------------------------------------------------
    def get(self, tenant_state_id: str, transfer_id: str) -> TransferApplication:
        try:
            return self._transfers.setdefault(tenant_state_id, {})[transfer_id]
        except KeyError:
            raise TransferNotFoundError(f"unknown transfer {transfer_id!r}") from None

    def get_consent(self, tenant_state_id: str, consent_id: str) -> ConsentInstrument:
        try:
            return self._consents.setdefault(tenant_state_id, {})[consent_id]
        except KeyError:
            raise TransferNotFoundError(f"unknown consent {consent_id!r}") from None

    @staticmethod
    def _default_prev_hash(tenant_state_id: str, parcel_id: UUID) -> str:
        return hashlib.sha256(
            f"genesis|{tenant_state_id}|{parcel_id}".encode("utf-8")
        ).hexdigest()

    # -- consent instruments ---------------------------------------------------
    def issue_consent(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        expires_at: datetime,
        actor: str,
    ) -> ConsentInstrument:
        """Issue a governor consent instrument for a dealing on the parcel."""
        if self._repo.get(tenant_state_id, parcel_id) is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        consent = ConsentInstrument(
            consent_id=f"consent-{tenant_state_id}-{next(self._consent_seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            expires_at=expires_at,
            issued_by=actor,
            issued_at=self._clock(),
        )
        self._consents.setdefault(tenant_state_id, {})[consent.consent_id] = consent
        self._log.record(
            "GOVERNOR_CONSENT_ISSUED",
            tenant_state_id,
            parcel_id=parcel_id,
            actor=actor,
            detail={"consent_id": consent.consent_id,
                    "expires_at": expires_at.isoformat()},
        )
        return consent

    def _consume_consent(self, application: TransferApplication, consent_id: str) -> None:
        if application.instrument_type not in CONSENT_REQUIRED:
            return  # stage auto-passes for non-consent dealings
        if not consent_id:
            raise ConsentError(
                f"instrument {application.instrument_type.value} requires a "
                "governor consent instrument id"
            )
        consent = self.get_consent(application.tenant_state_id, consent_id)
        if consent.parcel_id != application.parcel_id:
            raise ConsentError(
                f"consent {consent_id} was issued for parcel {consent.parcel_id}, "
                f"not {application.parcel_id}"
            )
        if consent.consumed:
            raise ConsentError(
                f"consent {consent_id} was already consumed by transfer "
                f"{consent.consumed_by} (single-use instrument)"
            )
        if self._clock() >= consent.expires_at:
            raise ConsentError(
                f"consent {consent_id} expired at {consent.expires_at.isoformat()}"
            )
        consent.consumed_by = application.transfer_id  # consumed exactly once
        application.consent_id = consent_id

    # -- application ----------------------------------------------------------
    def apply(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        instrument_type: TransferInstrumentType,
        transferor_stin: str,
        transferee_stin: str,
        evidence_document_id: str,
        actor: str,
        detail: Optional[dict] = None,
    ) -> TransferApplication:
        """Lodge a transfer application (APPLICATION stage) with all guards."""
        record = self._repo.get(tenant_state_id, parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        if record.status not in TRANSFERABLE_STATUSES:
            raise TransferError(
                f"parcel {parcel_id} has status {record.status.value}; only "
                "ACTIVE/REGISTERED parcels can be transferred"
            )
        # Fail-closed guards: encumbrances and disputes freeze dealings (409).
        self._eguard.assert_clear(tenant_state_id, parcel_id)
        self._dguard.assert_clear(tenant_state_id, parcel_id)
        if transferor_stin != record.owner_stin:
            raise TransferError(
                f"transferor {transferor_stin!r} does not match the current "
                f"registered owner {record.owner_stin!r}"
            )
        application = TransferApplication(
            transfer_id=f"transfer-{tenant_state_id}-{next(self._id_seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            instrument_type=instrument_type,
            transferor_stin=transferor_stin,
            transferee_stin=transferee_stin,
            evidence_document_id=evidence_document_id,
            stage=TransferStage.APPLICATION,
            created_at=self._clock(),
            applicant=actor,
            detail=detail or {},
        )
        application.history.append(
            {"stage": TransferStage.APPLICATION.value, "actor": actor,
             "note": "transfer application lodged",
             "at": self._clock().isoformat()}
        )
        self._transfers.setdefault(tenant_state_id, {})[application.transfer_id] = application
        self._log.record(
            "TRANSFER_APPLICATION",
            tenant_state_id,
            parcel_id=parcel_id,
            actor=actor,
            detail={"transfer_id": application.transfer_id,
                    "instrument_type": instrument_type.value,
                    "transferor_stin": transferor_stin,
                    "transferee_stin": transferee_stin},
        )
        return application

    # -- signal handler (Temporal signal equivalent) ---------------------------
    def advance(
        self,
        tenant_state_id: str,
        transfer_id: str,
        *,
        actor: str,
        consent_id: Optional[str] = None,
        note: str = "",
    ) -> TransferApplication:
        """Advance one stage, running the stage's activity (fail-closed)."""
        application = self.get(tenant_state_id, transfer_id)
        current = application.stage

        if current == TransferStage.APPLICATION:
            # Activity: verify the evidence document against mod-land-docs.
            result = self._docs.verify_document(
                tenant_state_id=tenant_state_id,
                document_id=application.evidence_document_id,
            )
            if not result.verified:
                raise EvidenceVerificationError(
                    f"evidence document {application.evidence_document_id!r} "
                    f"failed land-docs verification: {result.detail}"
                )
            TransferWorkflow.advance(application, actor=actor, note=note or result.detail)
        elif current == TransferStage.EVIDENCE_VERIFICATION:
            # Activity: consume the governor consent instrument (single-use).
            self._consume_consent(application, consent_id or "")
            TransferWorkflow.advance(application, actor=actor, note=note)
        elif current == TransferStage.CONSENT:
            # Activity: obtain tax clearance for the dealing.
            clearance = self._tax.clear(
                tenant_state_id=tenant_state_id,
                parcel_id=str(application.parcel_id),
                transfer_id=transfer_id,
            )
            application.tax_clearance_id = clearance.clearance_id
            TransferWorkflow.advance(
                application, actor=actor, note=note or clearance.clearance_id
            )
        elif current == TransferStage.TAX_CLEARANCE:
            # Activity: atomic registration (sign + ownership update + titles).
            self._apply_registration(application, actor=actor)
            TransferWorkflow.advance(application, actor=actor, note=note or "registered")
        else:
            raise TransferError(f"transfer {transfer_id} is already REGISTERED (terminal)")
        return application

    # -- registration activity ---------------------------------------------------
    def _mint_c_of_o(self, tenant_state_id: str, now: datetime) -> str:
        return f"{tenant_state_id.upper()}/COFO/{now.year}/{self._seq():06d}"

    def _apply_registration(
        self, application: TransferApplication, *, actor: str
    ) -> None:
        """Atomically: sign the transfer JWS (chained to the previous title
        hash), update ownership, issue the new title, mark the old title
        REPLACED (never deleted), and append TITLE_TRANSFERRED to the log."""
        record = self._repo.get(application.tenant_state_id, application.parcel_id)
        if record is None:
            raise TransferError(f"parcel {application.parcel_id} not found")
        # Re-check guards at registration time (fail-closed against races).
        self._eguard.assert_clear(application.tenant_state_id, application.parcel_id)
        self._dguard.assert_clear(application.tenant_state_id, application.parcel_id)

        now = self._clock()
        prev_hash = self._previous_title_hash(
            application.tenant_state_id, application.parcel_id
        )
        application.previous_title_hash = prev_hash
        payload = {
            "doc_type": "TITLE_TRANSFER",
            "issuer": f"ng.sos.{application.tenant_state_id}.lands-registry",
            "tenant_state_id": application.tenant_state_id,
            "parcel_id": str(application.parcel_id),
            "parcel_uin": record.parcel_uin,
            "instrument_type": application.instrument_type.value,
            "transfer_id": application.transfer_id,
            "transferor_stin": application.transferor_stin,
            "transferee_stin": application.transferee_stin,
            "evidence_document_id": application.evidence_document_id,
            "consent_id": application.consent_id,
            "tax_clearance_id": application.tax_clearance_id,
            "previous_title_sha256": prev_hash,
            "registered_at": now.isoformat(),
        }
        transfer_jws = signing.sign_payload(payload, self._registry_key)
        application.transfer_jws = transfer_jws

        new_c_of_o = self._mint_c_of_o(application.tenant_state_id, now)
        application.new_c_of_o_number = new_c_of_o
        old_c_of_o = record.c_of_o_number

        # Atomic ownership update: owner + new title number on the parcel row.
        updated = record.model_copy(
            update={"owner_stin": application.transferee_stin,
                    "c_of_o_number": new_c_of_o}
        )
        self._repo.update(updated)

        # Title register: old title REPLACED (never deleted), new title CURRENT.
        if old_c_of_o:
            if self._titles.find(application.tenant_state_id, old_c_of_o) is None:
                # Titles minted by the titling workflow enter the register here
                # so their REPLACED status is visible to deed verification.
                self._titles.register(
                    TitleRecord(
                        c_of_o_number=old_c_of_o,
                        tenant_state_id=application.tenant_state_id,
                        parcel_id=application.parcel_id,
                        owner_stin=application.transferor_stin,
                        title_jws="",
                        instrument="E_C_OF_O",
                        status=TitleStatus.CURRENT,
                        issued_at=now.isoformat(),
                    )
                )
            self._titles.mark_replaced(
                application.tenant_state_id, old_c_of_o, replaced_by=new_c_of_o
            )
        self._titles.register(
            TitleRecord(
                c_of_o_number=new_c_of_o,
                tenant_state_id=application.tenant_state_id,
                parcel_id=application.parcel_id,
                owner_stin=application.transferee_stin,
                title_jws=transfer_jws,
                instrument=application.instrument_type.value,
                status=TitleStatus.CURRENT,
                issued_at=now.isoformat(),
            )
        )
        self._log.record(
            "TITLE_TRANSFERRED",
            application.tenant_state_id,
            parcel_id=application.parcel_id,
            actor=actor,
            detail={
                "transfer_id": application.transfer_id,
                "instrument_type": application.instrument_type.value,
                "from_owner_stin": application.transferor_stin,
                "to_owner_stin": application.transferee_stin,
                "old_c_of_o_number": old_c_of_o,
                "new_c_of_o_number": new_c_of_o,
                "previous_title_sha256": prev_hash,
                "transfer_jws": transfer_jws,
                **application.detail,
            },
        )

    # -- completion fast paths used by succession / court orders ----------------
    def complete_assent(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        transferor_stin: str,
        transferee_stin: str,
        evidence_document_id: str,
        actor: str,
        detail: Optional[dict] = None,
    ) -> TransferApplication:
        """Register a transmission assent through the same transfer code path."""
        return self._complete_direct(
            tenant_state_id, parcel_id,
            instrument_type=TransferInstrumentType.ASSENT,
            transferor_stin=transferor_stin, transferee_stin=transferee_stin,
            evidence_document_id=evidence_document_id, actor=actor, detail=detail,
        )

    def complete_court_order(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        new_owner_stin: str,
        order_number: str,
        actor: str,
        detail: Optional[dict] = None,
    ) -> TransferApplication:
        """Register a court-ordered ownership mutation through the transfer path."""
        record = self._repo.get(tenant_state_id, parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        return self._complete_direct(
            tenant_state_id, parcel_id,
            instrument_type=TransferInstrumentType.COURT_ORDER,
            transferor_stin=record.owner_stin, transferee_stin=new_owner_stin,
            evidence_document_id=order_number, actor=actor, detail=detail,
        )

    def _complete_direct(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        instrument_type: TransferInstrumentType,
        transferor_stin: str,
        transferee_stin: str,
        evidence_document_id: str,
        actor: str,
        detail: Optional[dict],
    ) -> TransferApplication:
        """Pre-authorized completion: application → REGISTERED in one code path.

        Used by transmission (ASSENT) and court-order dealings whose upstream
        workflows already performed evidence/consent/review verification, so
        history, title update, and the TITLE_TRANSFERRED event stay unified.
        """
        record = self._repo.get(tenant_state_id, parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        if record.status not in TRANSFERABLE_STATUSES:
            raise TransferError(
                f"parcel {parcel_id} has status {record.status.value}; only "
                "ACTIVE/REGISTERED parcels can be transferred"
            )
        application = TransferApplication(
            transfer_id=f"transfer-{tenant_state_id}-{next(self._id_seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            instrument_type=instrument_type,
            transferor_stin=transferor_stin,
            transferee_stin=transferee_stin,
            evidence_document_id=evidence_document_id,
            stage=TransferStage.TAX_CLEARANCE,
            created_at=self._clock(),
            applicant=actor,
            detail=detail or {},
        )
        application.history.append(
            {"stage": TransferStage.APPLICATION.value, "actor": actor,
             "note": f"pre-authorized {instrument_type.value} dealing",
             "at": self._clock().isoformat()}
        )
        self._transfers.setdefault(tenant_state_id, {})[application.transfer_id] = application
        self._apply_registration(application, actor=actor)
        application.stage = TransferStage.REGISTERED
        application.history.append(
            {"stage": TransferStage.REGISTERED.value, "actor": actor,
             "note": "registered", "at": self._clock().isoformat()}
        )
        return application
