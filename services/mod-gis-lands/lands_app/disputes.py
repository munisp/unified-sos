"""Cadastral dispute management — lifecycle, guard, and audit-chain recording.

Lifecycle::

    OPENED ──review──> UNDER_REVIEW ──resolve──> RESOLVED
      │                    │
      └──dismiss──> DISMISSED <──dismiss──┘

While a parcel has an OPENED/UNDER_REVIEW dispute, the
:class:`DisputeGuard` blocks titling approvals, subdivision, and merger
(HTTP 409 at the API layer) — a contested title must never be advanced or
mutated until the dispute is RESOLVED or DISMISSED. Every transition is
recorded in the hash-chained cadastre event log (:mod:`lands_app.eventlog`).
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from .eventlog import CadastreEventLog


class DisputeGrounds(str, enum.Enum):
    """Legal grounds for lodging a cadastral dispute."""

    BOUNDARY = "BOUNDARY"
    OWNERSHIP = "OWNERSHIP"
    FRAUD = "FRAUD"
    INHERITANCE = "INHERITANCE"
    OTHER = "OTHER"


class DisputeStatus(str, enum.Enum):
    OPENED = "OPENED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


#: Dispute states that freeze the parcel (block titling/subdivision/merger).
OPEN_STATES = (DisputeStatus.OPENED, DisputeStatus.UNDER_REVIEW)


class DisputeError(Exception):
    """Base error for dispute operations."""


class DisputeActiveError(DisputeError):
    """A parcel with an open/under-review dispute was targeted for mutation."""


class DuplicateDisputeError(DisputeError):
    """A second open dispute was lodged against the same parcel."""


class IllegalTransitionError(DisputeError):
    """A dispute status transition that violates the lifecycle."""


@dataclass
class Dispute:
    """One land dispute case (row mirror of cadastre.disputes)."""

    dispute_id: str
    tenant_state_id: str
    parcel_id: UUID
    complainant: str
    grounds: DisputeGrounds
    description: str
    status: DisputeStatus
    opened_at: datetime
    updated_at: datetime
    resolver: Optional[str] = None
    resolution_note: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "dispute_id": self.dispute_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "complainant": self.complainant,
            "grounds": self.grounds.value,
            "description": self.description,
            "status": self.status.value,
            "resolver": self.resolver,
            "resolution_note": self.resolution_note,
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class DisputeStore:
    """Tenant-isolated in-memory dispute store with audit-chain recording."""

    def __init__(
        self,
        event_log: Optional[CadastreEventLog] = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._log = event_log
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # tenant_state_id -> dispute_id -> Dispute
        self._by_id: dict[str, dict[str, Dispute]] = {}
        self._seq = itertools.count(1)

    def _tenant(self, tenant_state_id: str) -> dict[str, Dispute]:
        return self._by_id.setdefault(tenant_state_id, {})

    def _record(self, event_type: str, dispute: Dispute, actor: str, detail: dict) -> None:
        if self._log is not None:
            self._log.record(
                event_type,
                dispute.tenant_state_id,
                parcel_id=dispute.parcel_id,
                actor=actor,
                detail={"dispute_id": dispute.dispute_id, **detail},
            )

    # -- lifecycle ---------------------------------------------------------
    def open(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        complainant: str,
        grounds: DisputeGrounds,
        description: str,
    ) -> Dispute:
        """Lodge a dispute; one open case per parcel at a time."""
        if self.has_open(tenant_state_id, parcel_id):
            raise DuplicateDisputeError(
                f"parcel {parcel_id} already has an open/under-review dispute"
            )
        now = self._clock()
        dispute = Dispute(
            dispute_id=f"dispute-{tenant_state_id}-{next(self._seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            complainant=complainant,
            grounds=grounds,
            description=description,
            status=DisputeStatus.OPENED,
            opened_at=now,
            updated_at=now,
        )
        self._tenant(tenant_state_id)[dispute.dispute_id] = dispute
        self._record(
            "DISPUTE_OPENED", dispute, complainant,
            {"grounds": grounds.value, "description": description},
        )
        return dispute

    def _transition(
        self,
        dispute: Dispute,
        target: DisputeStatus,
        *,
        actor: str,
        allowed: tuple[DisputeStatus, ...],
        resolution_note: Optional[str] = None,
    ) -> Dispute:
        if dispute.status not in allowed:
            raise IllegalTransitionError(
                f"cannot move dispute {dispute.dispute_id} from "
                f"{dispute.status.value} to {target.value}"
            )
        dispute.status = target
        dispute.updated_at = self._clock()
        if target in (DisputeStatus.RESOLVED, DisputeStatus.DISMISSED):
            dispute.resolver = actor
            dispute.resolution_note = resolution_note
        self._record(
            f"DISPUTE_{target.value}", dispute, actor,
            {"resolution_note": resolution_note or ""},
        )
        return dispute

    def review(self, tenant_state_id: str, dispute_id: str, *, actor: str) -> Dispute:
        """OPENED → UNDER_REVIEW (case taken up by the land tribunal)."""
        return self._transition(
            self.get(tenant_state_id, dispute_id), DisputeStatus.UNDER_REVIEW,
            actor=actor, allowed=(DisputeStatus.OPENED,),
        )

    def resolve(
        self, tenant_state_id: str, dispute_id: str, *, resolver: str, resolution_note: str
    ) -> Dispute:
        """OPENED/UNDER_REVIEW → RESOLVED with a resolution note."""
        return self._transition(
            self.get(tenant_state_id, dispute_id), DisputeStatus.RESOLVED,
            actor=resolver, allowed=OPEN_STATES, resolution_note=resolution_note,
        )

    def dismiss(
        self, tenant_state_id: str, dispute_id: str, *, resolver: str, resolution_note: str = ""
    ) -> Dispute:
        """OPENED/UNDER_REVIEW → DISMISSED (no merit)."""
        return self._transition(
            self.get(tenant_state_id, dispute_id), DisputeStatus.DISMISSED,
            actor=resolver, allowed=OPEN_STATES, resolution_note=resolution_note,
        )

    # -- reads ---------------------------------------------------------------
    def get(self, tenant_state_id: str, dispute_id: str) -> Dispute:
        try:
            return self._tenant(tenant_state_id)[dispute_id]
        except KeyError:
            raise DisputeError(f"unknown dispute {dispute_id!r}") from None

    def list_for_parcel(self, tenant_state_id: str, parcel_id: UUID) -> list[Dispute]:
        return [
            d for d in self._tenant(tenant_state_id).values() if d.parcel_id == parcel_id
        ]

    def has_open(self, tenant_state_id: str, parcel_id: UUID) -> bool:
        return any(
            d.status in OPEN_STATES for d in self.list_for_parcel(tenant_state_id, parcel_id)
        )


class DisputeGuard:
    """Fail-closed guard: blocks title mutations on disputed parcels (409)."""

    def __init__(self, store: DisputeStore) -> None:
        self._store = store

    def assert_clear(self, tenant_state_id: str, parcel_id: UUID) -> None:
        """Raise DisputeActiveError if the parcel has an open dispute."""
        if self._store.has_open(tenant_state_id, parcel_id):
            raise DisputeActiveError(
                f"parcel {parcel_id} has an open/under-review dispute; "
                "titling approvals, subdivision and merger are frozen until "
                "the dispute is RESOLVED or DISMISSED"
            )
