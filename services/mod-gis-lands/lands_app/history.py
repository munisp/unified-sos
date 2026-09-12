"""Chain-of-title / ownership history projection.

Derives a chronological ownership-and-instrument history for a parcel from:

* the parcel registry row (:mod:`lands_app.repository`) — registration,
* the titling workflow transitions (:mod:`lands_app.titling`) — every stage
  decision plus issuance of the signed e-C-of-O,
* the subdivision/merger lineage recorded in the hash-chained cadastre event
  log (:mod:`lands_app.eventlog`) and ``parent_parcel_ids``.

Each entry carries (owner, instrument, from/to dates, tx reference) so the
registrar can render a full chain of title per parcel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from .eventlog import CadastreEventLog
from .repository import ParcelRepository
from .schemas import ParcelRecord
from .titling import LocalTitlingRunner, WorkflowError, WorkflowStatus


@dataclass(frozen=True)
class HistoryEntry:
    """One chain-of-title row."""

    parcel_id: str
    owner_stin: str
    instrument: str
    from_date: str
    to_date: Optional[str]
    tx_reference: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "parcel_id": self.parcel_id,
            "owner_stin": self.owner_stin,
            "instrument": self.instrument,
            "from_date": self.from_date,
            "to_date": self.to_date,
            "tx_reference": self.tx_reference,
            "detail": self.detail,
        }


def _registration_entry(record: ParcelRecord) -> HistoryEntry:
    return HistoryEntry(
        parcel_id=str(record.parcel_id),
        owner_stin=record.owner_stin,
        instrument="PARCEL_REGISTRATION",
        from_date="",  # refined below from the workflow clock when available
        to_date=None,
        tx_reference=record.parcel_uin,
        detail=f"parcel {record.parcel_uin} registered (survey plan {record.survey_plan_no})",
    )


def chain_of_title(
    tenant_state_id: str,
    parcel_id: UUID,
    repository: ParcelRepository,
    titling_runner: LocalTitlingRunner,
    event_log: CadastreEventLog,
) -> Optional[list[HistoryEntry]]:
    """Chronological chain-of-title entries for one parcel (tenant-scoped).

    Returns ``None`` when the parcel does not exist in this tenant.
    """
    record = repository.get(tenant_state_id, parcel_id)
    if record is None:
        return None

    entries: list[HistoryEntry] = [_registration_entry(record)]

    # -- titling workflow transitions -------------------------------------
    instance = None
    if record.titling_workflow_id:
        try:
            instance = titling_runner.get(record.titling_workflow_id)
        except WorkflowError:
            instance = None
    if instance is not None:
        for transition in instance.history:
            entries.append(
                HistoryEntry(
                    parcel_id=str(record.parcel_id),
                    owner_stin=record.owner_stin,
                    instrument=f"TITLING_{transition.stage.value}",
                    from_date=transition.entered_at.isoformat(),
                    to_date=transition.exited_at.isoformat(),
                    tx_reference=instance.workflow_id,
                    detail=transition.note
                    or f"{'approved' if transition.approved else 'rejected'} by {transition.actor}",
                )
            )
        if instance.status == WorkflowStatus.ISSUED:
            entries.append(
                HistoryEntry(
                    parcel_id=str(record.parcel_id),
                    owner_stin=record.owner_stin,
                    instrument="TITLE_ISSUED",
                    from_date=instance.history[-1].exited_at.isoformat(),
                    to_date=None,
                    tx_reference=instance.c_of_o_number or instance.workflow_id,
                    detail=f"e-C-of-O {instance.c_of_o_number} issued and Ed25519-signed",
                )
            )
        # Anchor the registration entry to the workflow start time.
        entries[0] = HistoryEntry(
            **{**entries[0].__dict__, "from_date": instance.started_at.isoformat()}
        )

    # -- subdivision / merger lineage --------------------------------------
    if record.parent_parcel_ids:
        entries.append(
            HistoryEntry(
                parcel_id=str(record.parcel_id),
                owner_stin=record.owner_stin,
                instrument="LINEAGE_DERIVATION",
                from_date=entries[0].from_date,
                to_date=None,
                tx_reference=record.parcel_uin,
                detail="derived from parent parcel(s) "
                + ", ".join(str(p) for p in record.parent_parcel_ids),
            )
        )

    # -- cadastre event log (disputes, subdivision, anchoring, ...) --------
    for event in event_log.events(tenant_state_id, parcel_id):
        entries.append(
            HistoryEntry(
                parcel_id=str(record.parcel_id),
                owner_stin=record.owner_stin,
                instrument=event.event_type,
                from_date=event.recorded_at,
                to_date=None,
                tx_reference=event.event_hash,
                detail=f"actor={event.actor} {event.detail}",
            )
        )

    # Chronological ordering; undated registration entry sorts first.
    entries.sort(key=lambda e: (e.from_date, e.instrument))
    return entries
