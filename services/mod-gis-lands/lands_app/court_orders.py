"""Court orders — vesting and rectification mutations ordered by a court.

Order types:

* ``VEST_TITLE`` — vest title in a named person (ownership mutation),
* ``RECTIFY_OWNER`` — rectify an erroneous owner entry (ownership mutation),
* ``RECTIFY_BOUNDARY`` — rectify the registered boundary (geometry mutation).

Every order carries the order number, court, instrument hash (SHA-256 of the
sealed order), and effective date, and requires **both** registrar and
Attorney-General approvals before it can be applied.

Application of an ownership mutation goes through the
:class:`~lands_app.transfers.TransferService` (``COURT_ORDER`` instrument) so
the signed instrument, title register update, and ``TITLE_TRANSFERRED`` event
stay in one code path. A boundary rectification preserves a
**pre-rectification snapshot** of the boundary in the hash-chained event log
(``BOUNDARY_RECTIFIED``) before the registry row is updated.
"""

from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from .eventlog import CadastreEventLog
from .geometry import GeometryError, area_sqm, parse_boundary
from .repository import ParcelRepository
from .transfers import TransferNotFoundError, TransferService


class CourtOrderType(str, enum.Enum):
    VEST_TITLE = "VEST_TITLE"
    RECTIFY_OWNER = "RECTIFY_OWNER"
    RECTIFY_BOUNDARY = "RECTIFY_BOUNDARY"


class CourtOrderStatus(str, enum.Enum):
    PENDING_APPROVALS = "PENDING_APPROVALS"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"


class CourtOrderError(Exception):
    """Illegal court-order operation (mapped to HTTP 409/422)."""


#: Approval roles required before an order may be applied.
REQUIRED_APPROVALS = ("registrar", "ag")


@dataclass
class CourtOrder:
    """One court order targeting a parcel."""

    order_id: str
    tenant_state_id: str
    parcel_id: UUID
    order_type: CourtOrderType
    order_number: str
    court: str
    instrument_hash: str
    effective_date: str
    status: CourtOrderStatus
    filed_by: str
    created_at: datetime
    new_owner_stin: Optional[str] = None
    new_boundary_geojson: Optional[dict[str, Any]] = None
    approvals: dict[str, str] = field(default_factory=dict)  # role -> actor
    transfer_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "tenant_state_id": self.tenant_state_id,
            "parcel_id": str(self.parcel_id),
            "order_type": self.order_type.value,
            "order_number": self.order_number,
            "court": self.court,
            "instrument_hash": self.instrument_hash,
            "effective_date": self.effective_date,
            "status": self.status.value,
            "filed_by": self.filed_by,
            "new_owner_stin": self.new_owner_stin,
            "approvals": dict(self.approvals),
            "transfer_id": self.transfer_id,
            "created_at": self.created_at.isoformat(),
        }


class CourtOrderService:
    """Orchestrates court orders against repo + transfer service + event log."""

    def __init__(
        self,
        repository: ParcelRepository,
        event_log: CadastreEventLog,
        transfer_service: TransferService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository
        self._log = event_log
        self._transfers = transfer_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._orders: dict[str, dict[str, CourtOrder]] = {}
        self._seq = itertools.count(1)

    def get(self, tenant_state_id: str, order_id: str) -> CourtOrder:
        try:
            return self._orders.setdefault(tenant_state_id, {})[order_id]
        except KeyError:
            raise TransferNotFoundError(f"unknown court order {order_id!r}") from None

    # -- filing -----------------------------------------------------------------
    def file_order(
        self,
        tenant_state_id: str,
        parcel_id: UUID,
        *,
        order_type: CourtOrderType,
        order_number: str,
        court: str,
        instrument_hash: str,
        effective_date: str,
        actor: str,
        new_owner_stin: Optional[str] = None,
        new_boundary_geojson: Optional[dict[str, Any]] = None,
    ) -> CourtOrder:
        """File a court order (PENDING_APPROVALS)."""
        record = self._repo.get(tenant_state_id, parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {parcel_id} not found")
        if order_type in (CourtOrderType.VEST_TITLE, CourtOrderType.RECTIFY_OWNER):
            if not new_owner_stin:
                raise CourtOrderError(
                    f"{order_type.value} orders require new_owner_stin"
                )
        if order_type == CourtOrderType.RECTIFY_BOUNDARY:
            if not new_boundary_geojson:
                raise CourtOrderError("RECTIFY_BOUNDARY orders require new_boundary_geojson")
            try:
                parse_boundary(new_boundary_geojson)
            except GeometryError as exc:
                raise CourtOrderError(f"invalid rectified boundary: {exc}") from exc
        order = CourtOrder(
            order_id=f"order-{tenant_state_id}-{next(self._seq):06d}",
            tenant_state_id=tenant_state_id,
            parcel_id=parcel_id,
            order_type=order_type,
            order_number=order_number,
            court=court,
            instrument_hash=instrument_hash,
            effective_date=effective_date,
            status=CourtOrderStatus.PENDING_APPROVALS,
            filed_by=actor,
            created_at=self._clock(),
            new_owner_stin=new_owner_stin,
            new_boundary_geojson=new_boundary_geojson,
        )
        self._orders.setdefault(tenant_state_id, {})[order.order_id] = order
        self._log.record(
            "COURT_ORDER_FILED",
            tenant_state_id,
            parcel_id=parcel_id,
            actor=actor,
            detail={"order_id": order.order_id, "order_number": order_number,
                    "order_type": order_type.value, "court": court},
        )
        return order

    # -- approvals -------------------------------------------------------------
    def approve(
        self, tenant_state_id: str, order_id: str, *, actor: str, role: str
    ) -> CourtOrder:
        """Record a registrar/AG approval (one per role)."""
        order = self.get(tenant_state_id, order_id)
        if order.status != CourtOrderStatus.PENDING_APPROVALS:
            raise CourtOrderError(
                f"order {order_id} is {order.status.value}; approvals are closed"
            )
        if role not in REQUIRED_APPROVALS:
            raise CourtOrderError(
                f"unknown approval role {role!r}; expected one of {REQUIRED_APPROVALS}"
            )
        if role in order.approvals:
            raise CourtOrderError(f"order {order_id} already has a {role} approval")
        order.approvals[role] = actor
        self._log.record(
            "COURT_ORDER_APPROVED",
            tenant_state_id,
            parcel_id=order.parcel_id,
            actor=actor,
            detail={"order_id": order_id, "role": role},
        )
        if all(r in order.approvals for r in REQUIRED_APPROVALS):
            order.status = CourtOrderStatus.APPROVED
        return order

    # -- application -------------------------------------------------------------
    def apply(self, tenant_state_id: str, order_id: str, *, actor: str) -> CourtOrder:
        """Apply an approved order: register the mutation."""
        order = self.get(tenant_state_id, order_id)
        if order.status != CourtOrderStatus.APPROVED:
            raise CourtOrderError(
                f"order {order_id} is {order.status.value}; registrar and AG "
                "approvals are required before application"
            )
        record = self._repo.get(tenant_state_id, order.parcel_id)
        if record is None:
            raise TransferNotFoundError(f"parcel {order.parcel_id} not found")

        if order.order_type in (CourtOrderType.VEST_TITLE, CourtOrderType.RECTIFY_OWNER):
            application = self._transfers.complete_court_order(
                tenant_state_id,
                order.parcel_id,
                new_owner_stin=order.new_owner_stin or "",
                order_number=order.order_number,
                actor=actor,
                detail={"order_id": order.order_id, "court": order.court,
                        "order_type": order.order_type.value,
                        "instrument_hash": order.instrument_hash,
                        "effective_date": order.effective_date},
            )
            order.transfer_id = application.transfer_id
        else:  # RECTIFY_BOUNDARY — geometry mutation with pre-rectification snapshot
            assert order.new_boundary_geojson is not None
            geom = parse_boundary(order.new_boundary_geojson)
            new_area = round(area_sqm(geom), 2)
            self._log.record(
                "BOUNDARY_RECTIFIED",
                tenant_state_id,
                parcel_id=order.parcel_id,
                actor=actor,
                detail={
                    "order_id": order.order_id,
                    "order_number": order.order_number,
                    "court": order.court,
                    "instrument_hash": order.instrument_hash,
                    "effective_date": order.effective_date,
                    "pre_rectification_snapshot": {
                        "boundary_geojson": record.boundary_geojson,
                        "area_sqm": record.area_sqm,
                        "beacon_count": record.beacon_count,
                    },
                    "new_area_sqm": new_area,
                },
            )
            updated = record.model_copy(
                update={
                    "boundary_geojson": order.new_boundary_geojson,
                    "area_sqm": new_area,
                    "beacon_count": max(
                        3, len(order.new_boundary_geojson["coordinates"][0]) - 1
                    ),
                }
            )
            self._repo.update(updated)
        order.status = CourtOrderStatus.APPLIED
        self._log.record(
            "COURT_ORDER_APPLIED",
            tenant_state_id,
            parcel_id=order.parcel_id,
            actor=actor,
            detail={"order_id": order.order_id, "order_number": order.order_number,
                    "order_type": order.order_type.value},
        )
        return order
