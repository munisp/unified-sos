"""Encumbrance register — mortgages, caveats, cautions, lis pendens, etc.

An **encumbrance** is a registered interest or claim against a parcel that
freezes dealings (titling approvals, subdivision, merger, transfer
registration) until it is released or withdrawn. Types:

* ``MORTGAGE`` — charge securing a loan,
* ``CAVEAT`` / ``CAUTION`` — notice of a claimed interest,
* ``LIS_PENDENS`` — pending litigation notice,
* ``COURT_ORDER`` — freezing/order instrument,
* ``LEASE`` — registered leasehold interest.

Each entry carries a **priority** (lower number = earlier/senior claim), the
**instrument hash** (SHA-256 of the source document), and an optional
**expiry**. Released/withdrawn/expired entries are retained for audit — the
register is append-only like the event log. Every transition is recorded in
the hash-chained cadastre event log.

:class:`EncumbranceGuard` is the fail-closed guard wired into titling
decisions, subdivision, merger, and transfer registration (HTTP 409).
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from .eventlog import CadastreEventLog


class EncumbranceType(str, enum.Enum):
    MORTGAGE = "MORTGAGE"
    CAVEAT = "CAVEAT"
    CAUTION = "CAUTION"
    LIS_PENDENS = "LIS_PENDENS"
    COURT_ORDER = "COURT_ORDER"
    LEASE = "LEASE"


class EncumbranceStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"  # obligation discharged by the encumbrancer
    WITHDRAWN = "WITHDRAWN"  # entry withdrawn by the party who lodged it
    EXPIRED = "EXPIRED"  # lapsed past expires_at


class EncumbranceError(Exception):
    """Base error for encumbrance operations."""


class EncumbranceNotFoundError(EncumbranceError):
    """Unknown encumbrance id (mapped to HTTP 404)."""


class EncumbranceActiveError(EncumbranceError):
    """A parcel with an active encumbrance was targeted for mutation (409)."""


class IllegalEncumbranceTransitionError(EncumbranceError):
    """Release/withdraw of a non-active encumbrance (409)."""


@dataclass
class Encumbrance:
    """One encumbrance register entry (row mirror of cadastre.encumbrances)."""

    encumbrance_id: str
    tenant_state_id: str
    parcel_id: UUID
    type: EncumbranceType
    priority: int
    instrument_hash: str
    status: EncumbranceStatus
    registered_by: str
    registered_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    closure_reason: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "encumbrance_id": self.encumbrance_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "type": self.type.value,
            "priority": self.priority,
            "instrument_hash": self.instrument_hash,
            "status": self.status.value,
            "registered_by": self.registered_by,
            "registered_at": self.registered_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "closed_by": self.closed_by,
            "closure_reason": self.closure_reason,
        }


class EncumbranceStore:
    """Tenant-isolated in-memory encumbrance register with audit-chain recording."""

    def __init__(
        self,
        event_log: Optional[CadastreEventLog] = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._log = event_log
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # tenant_state_id -> encumbrance_id -> Encumbrance
        self._by_id: dict[str, dict[str, Encumbrance]] = {}
        self._seq = itertools.count(1)

    def _tenant(self, tenant_state_id: str) -> dict[str, Encumbrance]:
        return self._by_id.setdefault(tenant_state_id, {})

    def _record(self, event_type: str, enc: Encumbrance, actor: str, detail: dict) -> None:
        if self._log is not None:
            self._log.record(
                event_type,
                enc.tenant_state_id,
                parcel_id=enc.parcel_id,
                actor=actor,
                detail={"encumbrance_id": enc.encumbrance_id, **detail},
            )

    # -- lifecycle ---------------------------------------------------------
    def register(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        type: EncumbranceType,
        instrument_hash: str,
        priority: int = 0,
        expires_at: Optional[datetime] = None,
        actor: str,
    ) -> Encumbrance:
        """Enter an encumbrance on the register (status ACTIVE)."""
        now = self._clock()
        enc = Encumbrance(
            encumbrance_id=f"enc-{tenant_state_id}-{next(self._seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            type=type,
            priority=priority,
            instrument_hash=instrument_hash,
            status=EncumbranceStatus.ACTIVE,
            registered_by=actor,
            registered_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self._tenant(tenant_state_id)[enc.encumbrance_id] = enc
        self._record(
            "ENCUMBRANCE_REGISTERED", enc, actor,
            {"type": type.value, "priority": priority,
             "instrument_hash": instrument_hash,
             "expires_at": expires_at.isoformat() if expires_at else None},
        )
        return enc

    def _close(
        self,
        enc: Encumbrance,
        target: EncumbranceStatus,
        *,
        actor: str,
        reason: str,
    ) -> Encumbrance:
        self._refresh_expiry(enc)
        if enc.status != EncumbranceStatus.ACTIVE:
            raise IllegalEncumbranceTransitionError(
                f"encumbrance {enc.encumbrance_id} is {enc.status.value}; "
                "only ACTIVE encumbrances can be released or withdrawn"
            )
        enc.status = target
        enc.updated_at = self._clock()
        enc.closed_by = actor
        enc.closure_reason = reason
        self._record(
            f"ENCUMBRANCE_{target.value}", enc, actor, {"reason": reason},
        )
        return enc

    def release(
        self, tenant_state_id: str, encumbrance_id: str, *, actor: str, reason: str = ""
    ) -> Encumbrance:
        """Discharge an encumbrance (e.g. mortgage paid off)."""
        return self._close(
            self.get(tenant_state_id, encumbrance_id), EncumbranceStatus.RELEASED,
            actor=actor, reason=reason,
        )

    def withdraw(
        self, tenant_state_id: str, encumbrance_id: str, *, actor: str, reason: str = ""
    ) -> Encumbrance:
        """Withdraw a lodged caveat/caution."""
        return self._close(
            self.get(tenant_state_id, encumbrance_id), EncumbranceStatus.WITHDRAWN,
            actor=actor, reason=reason,
        )

    # -- reads ---------------------------------------------------------------
    def _refresh_expiry(self, enc: Encumbrance) -> None:
        """Lazily lapse an ACTIVE encumbrance whose expiry has passed."""
        if (
            enc.status == EncumbranceStatus.ACTIVE
            and enc.expires_at is not None
            and self._clock() >= enc.expires_at
        ):
            enc.status = EncumbranceStatus.EXPIRED
            enc.updated_at = self._clock()
            enc.closure_reason = "expired"
            self._record("ENCUMBRANCE_EXPIRED", enc, "lands-registry", {})

    def get(self, tenant_state_id: str, encumbrance_id: str) -> Encumbrance:
        try:
            enc = self._tenant(tenant_state_id)[encumbrance_id]
        except KeyError:
            raise EncumbranceNotFoundError(
                f"unknown encumbrance {encumbrance_id!r}"
            ) from None
        self._refresh_expiry(enc)
        return enc

    def list_for_parcel(
        self, tenant_state_id: str, parcel_id: UUID, *, include_closed: bool = False
    ) -> list[Encumbrance]:
        """All register entries for a parcel, ordered by priority then entry."""
        entries = [
            e for e in self._tenant(tenant_state_id).values() if e.parcel_id == parcel_id
        ]
        for enc in entries:
            self._refresh_expiry(enc)
        if not include_closed:
            entries = [e for e in entries if e.status == EncumbranceStatus.ACTIVE]
        return sorted(entries, key=lambda e: (e.priority, e.registered_at))

    def active_for(self, tenant_state_id: str, parcel_id: UUID) -> list[Encumbrance]:
        return self.list_for_parcel(tenant_state_id, parcel_id, include_closed=False)


class EncumbranceGuard:
    """Fail-closed guard: blocks dealings on encumbered parcels (HTTP 409)."""

    def __init__(self, store: EncumbranceStore) -> None:
        self._store = store

    def assert_clear(self, tenant_state_id: str, parcel_id: UUID) -> None:
        """Raise EncumbranceActiveError if the parcel has active encumbrances."""
        active = self._store.active_for(tenant_state_id, parcel_id)
        if active:
            kinds = ", ".join(f"{e.type.value}({e.encumbrance_id})" for e in active)
            raise EncumbranceActiveError(
                f"parcel {parcel_id} has active encumbrance(s): {kinds}; "
                "titling approvals, subdivision, merger and transfer "
                "registration are frozen until release/withdrawal/expiry"
            )
