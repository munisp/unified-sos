"""Domain models for mod-transport-wim — WIM enforcement & corridor haulage."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CorridorConfig(BaseModel):
    """Gazetted axle/GVW limits and fine schedule for one corridor.

    Values are per-corridor policy-pack configuration (never hard-coded in
    binaries — CONTRIBUTING.md rule 1).
    """

    corridor_id: str
    state_id: str
    single_axle_limit_kg: float = Field(gt=0)
    tandem_axle_limit_kg: float = Field(gt=0)
    gvw_limit_kg: float = Field(gt=0, description="gross vehicle weight limit")
    fine_base_kobo: int = Field(ge=0)
    fine_per_overload_kg_kobo: int = Field(ge=0)
    tolerance_pct: float = Field(default=0.0, ge=0, le=20)


class WIMReading(BaseModel):
    """High-speed weigh-in-motion sensor reading from a piezo/bending-plate site."""

    reading_id: str
    corridor_id: str
    station_id: str
    axle_weights_kg: List[float]
    speed_kmh: float = Field(ge=0)
    vehicle_plate: Optional[str] = Field(
        default=None, description="populated directly or via ANPR correlation"
    )
    recorded_at: datetime = Field(default_factory=utcnow)

    @property
    def gvw_kg(self) -> float:
        return sum(self.axle_weights_kg)


class AxleViolation(BaseModel):
    axle_index: int
    weight_kg: float
    limit_kg: float
    overload_kg: float


class OverloadVerdict(BaseModel):
    reading_id: str
    corridor_id: str
    gvw_kg: float
    overload_detected: bool
    axle_violations: List[AxleViolation]
    gvw_overload_kg: float = 0.0


class FineStatus(str, enum.Enum):
    ASSESSED = "ASSESSED"
    NOTIFIED = "NOTIFIED"
    PAID = "PAID"
    VOIDED = "VOIDED"


class FineAssessment(BaseModel):
    """Automatic overload fine (TigerBeetle transfer code 120)."""

    fine_id: str
    reading_id: str
    corridor_id: str
    state_id: str
    vehicle_plate: Optional[str]
    total_overload_kg: float
    amount_kobo: int = Field(ge=0)
    transfer_code: int = 120  # haulage / WIM penalty (chart-of-accounts)
    status: FineStatus = FineStatus.ASSESSED
    assessed_at: datetime = Field(default_factory=utcnow)


class ANPREvent(BaseModel):
    """ANPR camera plate sighting used to attach identity to WIM readings."""

    event_id: str
    corridor_id: str
    camera_id: str
    vehicle_plate: str
    captured_at: datetime = Field(default_factory=utcnow)


class WeighbridgeEvent(BaseModel):
    """Shared envelope ``ng.sos.mining.weighbridge_reading`` (AsyncAPI)."""

    state_id: str
    station_id: str
    axle_weights_kg: List[float]
    gross_weight_kg: float
    anpr_plate: Optional[str] = None
    overload_detected: bool
    timestamp: datetime = Field(default_factory=utcnow)


class EManifest(BaseModel):
    """Haulage e-manifest presented at checkpoints."""

    manifest_id: str
    state_id: str
    vehicle_plate: str
    consignment_refs: List[str]
    cargo_description: str
    origin: str
    destination: str
    valid: bool = True
    issued_at: datetime = Field(default_factory=utcnow)


class ManifestVerification(BaseModel):
    manifest_id: str
    found: bool
    valid: bool
    vehicle_plate: Optional[str] = None
    detail: Optional[str] = None
