"""Probate / transmission — succession of title on the death of an owner.

``TransmissionWorkflow`` moves a deceased proprietor's parcel to the named
beneficiaries::

    DEATH_REPORTED → PROBATE_VERIFICATION → REGISTRAR_REVIEW → AG_REVIEW
    → TRANSMISSION_REGISTERED

* DEATH_REPORTED requires a death-certificate document id, verified
  fail-closed against mod-land-docs via the
  :class:`~lands_app.legal_adapters.LandDocsAdapter` seam.
* Multiple beneficiaries are supported; they take as joint owners (their
  STINs are recorded on the instrument and the parcel row).
* TRANSMISSION_REGISTERED completes **through the TransferService** with an
  ``ASSENT`` instrument, so the signed instrument, title update, and
  chain-of-title history stay in one code path.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from .eventlog import CadastreEventLog
from .legal_adapters import LandDocsAdapter
from .repository import ParcelRepository
from .transfers import TransferApplication, TransferNotFoundError, TransferService


class TransmissionStage(str, enum.Enum):
    DEATH_REPORTED = "DEATH_REPORTED"
    PROBATE_VERIFICATION = "PROBATE_VERIFICATION"
    REGISTRAR_REVIEW = "REGISTRAR_REVIEW"
    AG_REVIEW = "AG_REVIEW"
    TRANSMISSION_REGISTERED = "TRANSMISSION_REGISTERED"


TRANSMISSION_STAGE_ORDER: tuple[TransmissionStage, ...] = (
    TransmissionStage.DEATH_REPORTED,
    TransmissionStage.PROBATE_VERIFICATION,
    TransmissionStage.REGISTRAR_REVIEW,
    TransmissionStage.AG_REVIEW,
    TransmissionStage.TRANSMISSION_REGISTERED,
)


class TransmissionError(Exception):
    """Illegal transmission operation (mapped to HTTP 409)."""


@dataclass
class TransmissionCase:
    """State of one transmission (probate) case."""

    transmission_id: str
    tenant_state_id: str
    parcel_id: UUID
    deceased_stin: str
    death_certificate_doc_id: str
    beneficiaries: list[str]
    stage: TransmissionStage
    reported_by: str
    created_at: datetime
    history: list[dict] = field(default_factory=list)
    transfer_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "transmission_id": self.transmission_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "deceased_stin": self.deceased_stin,
            "death_certificate_doc_id": self.death_certificate_doc_id,
            "beneficiaries": list(self.beneficiaries),
            "stage": self.stage.value,
            "reported_by": self.reported_by,
            "transfer_id": self.transfer_id,
            "history": list(self.history),
            "created_at": self.created_at.isoformat(),
        }


class TransmissionWorkflow:
    """Pure stage-transition rules for transmission (Temporal-ready, no I/O)."""

    @classmethod
    def advance(cls, case: TransmissionCase, *, actor: str, note: str = "") -> TransmissionStage:
        idx = TRANSMISSION_STAGE_ORDER.index(case.stage)
        if idx >= len(TRANSMISSION_STAGE_ORDER) - 1:
            raise TransmissionError(
                f"transmission {case.transmission_id} is already TRANSMISSION_REGISTERED"
            )
        nxt = TRANSMISSION_STAGE_ORDER[idx + 1]
        case.stage = nxt
        case.history.append(
            {"stage": nxt.value, "actor": actor, "note": note,
             "at": datetime.now(timezone.utc).isoformat()}
        )
        return nxt


class TransmissionService:
    """Orchestrates transmission cases against repo + docs adapter + transfers."""

    def __init__(
        self,
        repository: ParcelRepository,
        event_log: CadastreEventLog,
        docs_adapter: LandDocsAdapter,
        transfer_service: TransferService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository
        self._log = event_log
        self._docs = docs_adapter
        self._transfers = transfer_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cases: dict[str, dict[str, TransmissionCase]] = {}
        self._seq = itertools.count(1)

    def get(self, tenant_state_id: str, transmission_id: str) -> TransmissionCase:
        try:
            return self._cases.setdefault(tenant_state_id, {})[transmission_id]
        except KeyError:
            raise TransferNotFoundError(
                f"unknown transmission {transmission_id!r}"
            ) from None

    # -- DEATH_REPORTED -------------------------------------------------------
    def report_death(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        deceased_stin: str,
        death_certificate_doc_id: str,
        beneficiaries: list[str],
        actor: str,
    ) -> TransmissionCase:
        """Open a transmission case; the death certificate is verified fail-closed."""
        record = self._repo.get(tenant_state_id, parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        if deceased_stin != record.owner_stin:
            raise TransmissionError(
                f"deceased {deceased_stin!r} does not match the current "
                f"registered owner {record.owner_stin!r}"
            )
        if not beneficiaries:
            raise TransmissionError("at least one beneficiary is required")
        verification = self._docs.verify_document(
            tenant_state_id=tenant_state_id, document_id=death_certificate_doc_id
        )
        if not verification.verified:
            raise TransmissionError(
                f"death certificate {death_certificate_doc_id!r} failed "
                f"land-docs verification: {verification.detail}"
            )
        case = TransmissionCase(
            transmission_id=f"transmission-{tenant_state_id}-{next(self._seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            deceased_stin=deceased_stin,
            death_certificate_doc_id=death_certificate_doc_id,
            beneficiaries=list(beneficiaries),
            stage=TransmissionStage.DEATH_REPORTED,
            reported_by=actor,
            created_at=self._clock(),
        )
        case.history.append(
            {"stage": TransmissionStage.DEATH_REPORTED.value, "actor": actor,
             "note": "death reported; certificate verified",
             "at": self._clock().isoformat()}
        )
        self._cases.setdefault(tenant_state_id, {})[case.transmission_id] = case
        self._log.record(
            "TRANSMISSION_DEATH_REPORTED",
            tenant_state_id,
            parcel_id=parcel_id,
            actor=actor,
            detail={"transmission_id": case.transmission_id,
                    "deceased_stin": deceased_stin,
                    "beneficiaries": list(beneficiaries),
                    "death_certificate_doc_id": death_certificate_doc_id},
        )
        return case

    # -- signal handler ---------------------------------------------------------
    def advance(
        self,
        tenant_state_id: str,
        transmission_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> TransmissionCase:
        """Advance one stage; the final stage completes via the TransferService."""
        case = self.get(tenant_state_id, transmission_id)
        nxt = TransmissionWorkflow.advance(case, actor=actor, note=note)
        if nxt == TransmissionStage.TRANSMISSION_REGISTERED:
            application = self._transfers.complete_assent(
                tenant_state_id,
                case.parcel_id,
                transferor_stin=case.deceased_stin,
                transferee_stin="+".join(case.beneficiaries),
                evidence_document_id=case.death_certificate_doc_id,
                actor=actor,
                detail={
                    "transmission_id": case.transmission_id,
                    "beneficiaries": list(case.beneficiaries),
                },
            )
            case.transfer_id = application.transfer_id
            self._log.record(
                "TRANSMISSION_REGISTERED",
                tenant_state_id,
                parcel_id=case.parcel_id,
                actor=actor,
                detail={"transmission_id": case.transmission_id,
                        "transfer_id": application.transfer_id,
                        "beneficiaries": list(case.beneficiaries)},
            )
        return case
