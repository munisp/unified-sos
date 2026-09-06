"""Domain models for mod-mining.

HARD CONSTRAINT (docs/architecture/02-bounded-contexts.md): mining royalties
accrue to the **federation account**. State-competent levies and federal
royalty lines are **separated by construction**:

- ``LevyLine`` (state-competent, TigerBeetle transfer code 110, account
  classes 1xxx–4xxx) is the only monetary line this module can create.
- ``FederalRoyaltyLine`` exists for *reporting parity* only; it can never be
  attached to a levy assessment or a split rule — see ``levy.py``.
- Any split rule that names a royalty/federal beneficiary or a 5xxx account
  class is rejected at validation time.

Event payload field names follow ``contracts/asyncapi/mining-events.yaml``.
Monetary amounts are integer kobo.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MineralType(str, enum.Enum):
    """Enum values from contracts/asyncapi/mining-events.yaml."""

    LITHIUM_SPODUMENE = "LITHIUM_SPODUMENE"
    COLUMBITE = "COLUMBITE"
    TANTALITE = "TANTALITE"
    GOLD_ORE = "GOLD_ORE"
    GRANITE = "GRANITE"
    SAPPHIRE = "SAPPHIRE"
    BARITE = "BARITE"


class ConsignmentStatus(str, enum.Enum):
    """Lifecycle: create -> weighbridge reading -> dispatch -> delivery."""

    CREATED = "CREATED"
    WEIGHED = "WEIGHED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"


class MineralSite(BaseModel):
    site_id: str
    state_id: str = Field(description="nasarawa | osun | taraba | ogun")
    mine_lease_id: str = Field(description="e.g. ML-NAS-KOKO-04")
    operator_name: str
    minerals: list[MineralType]
    active: bool = True
    registered_at: datetime = Field(default_factory=utcnow)


class WeighbridgeReading(BaseModel):
    """Reading captured at the site weighbridge before dispatch."""

    station_id: str
    gross_weight_kg: float = Field(ge=0)
    tare_weight_kg: float = Field(ge=0)
    axle_weights_kg: list[float] = Field(default_factory=list)
    anpr_plate: Optional[str] = None
    taken_at: datetime = Field(default_factory=utcnow)

    @property
    def net_weight_kg(self) -> float:
        return self.gross_weight_kg - self.tare_weight_kg

    def validate_weights(self) -> None:
        if self.net_weight_kg <= 0:
            raise ValueError("net weight must be positive (gross > tare)")


class AssayRecord(BaseModel):
    """XRF assay sample attached to a consignment before dispatch."""

    assay_id: str
    lab_id: str
    lithium_oxide_grade_pct: Optional[float] = Field(default=None, ge=0, le=100)
    gold_grade_gpt: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None
    taken_at: datetime = Field(default_factory=utcnow)


class LevyLine(BaseModel):
    """A state-competent levy line (the ONLY payable line mod-mining emits).

    ``transfer_code`` 110 per ledger/chart-of-accounts.md; credited to
    account classes 1xxx–4xxx only — never 5xxx federal pass-through.
    """

    beneficiary: str = Field(description="e.g. STATE_CONSOLIDATED_REVENUE_FUND")
    tigerbeetle_account_code: int = Field(description="class 1xxx-4xxx")
    transfer_code: int = 110
    amount_kobo: int = Field(ge=0)


class FederalRoyaltyLine(BaseModel):
    """Federal royalty accrual — REPORTING ONLY.

    Royalties are federal (account class 5xxx pass-through). This module may
    estimate the federal accrual for reconciliation with the federation
    account, but the line can never enter a levy assessment or split rule.
    """

    tigerbeetle_account_code: int = 5001  # Federal Royalty Pass-Through
    amount_kobo: int = Field(ge=0)
    remitted_to: str = "FEDERATION_ACCOUNT"


class LevyAssessment(BaseModel):
    """State levy computed from tonnage + assay at dispatch time."""

    assessment_id: str
    consignment_id: str
    state_id: str
    lines: list[LevyLine]
    total_kobo: int = Field(ge=0)
    assessed_at: datetime = Field(default_factory=utcnow)
    # Federal royalty estimate kept strictly separate (informational only).
    federal_royalty_reference_kobo: Optional[int] = None


class Consignment(BaseModel):
    consignment_id: str = Field(description="e.g. MIN-NAS-2026-0819")
    state_id: str
    site_id: str
    mine_lease_id: str
    mineral_type: MineralType
    truck_registration: str
    rfid_seal_id: str
    destination_corridor: str
    status: ConsignmentStatus = ConsignmentStatus.CREATED
    weighbridge: Optional[WeighbridgeReading] = None
    assay: Optional[AssayRecord] = None
    levy: Optional[LevyAssessment] = None
    created_at: datetime = Field(default_factory=utcnow)
    dispatched_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None


class ConsignmentDispatchedEvent(BaseModel):
    """Payload for ``ng.sos.mining.consignment_dispatched`` (AsyncAPI contract)."""

    state_id: str
    consignment_id: str
    mine_lease_id: str
    mineral_type: MineralType
    gross_weight_kg: float
    tare_weight_kg: float
    net_weight_kg: float
    lithium_oxide_grade_pct: Optional[float] = None
    truck_registration: str
    rfid_seal_id: str
    royalty_due_kobo: int = Field(
        description="State levy (NOT federal royalty) due, per contract"
    )
    destination_corridor: str
    timestamp: datetime = Field(default_factory=utcnow)
