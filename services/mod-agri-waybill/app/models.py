"""Domain models for mod-agri-waybill — produce e-waybills & warehouse receipts."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProduceType(str, enum.Enum):
    YAM_TUBERS = "YAM_TUBERS"
    RICE_PADDY = "RICE_PADDY"
    MAIZE = "MAIZE"
    CASSAVA = "CASSAVA"
    SOYBEAN = "SOYBEAN"
    COCOA = "COCOA"
    TEA = "TEA"
    SESAME = "SESAME"


class WaybillStatus(str, enum.Enum):
    ISSUED = "ISSUED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class EWaybill(BaseModel):
    waybill_number: str = Field(description="e.g. WB-BEN-2026-000123")
    state_id: str
    consignor_id: str
    consignee_id: str
    produce_type: ProduceType
    quantity_kg: float = Field(ge=0)
    vehicle_plate: str
    origin: str
    destination: str
    levy_kobo: int = Field(ge=0, description="per-consignment produce levy")
    transfer_code: int = 140  # transit ticketing (chart-of-accounts)
    status: WaybillStatus = WaybillStatus.ISSUED
    qr_payload: Optional[str] = None  # set by the service at issuance
    issued_at: datetime = Field(default_factory=utcnow)
    delivered_at: Optional[datetime] = None


class TrackingEvent(BaseModel):
    """A checkpoint sighting along the consignment's route."""

    event_id: str
    waybill_number: str
    checkpoint_id: str
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    note: Optional[str] = None
    recorded_at: datetime = Field(default_factory=utcnow)


class WarehouseReceipt(BaseModel):
    """Agro-hub warehouse receipt — collateralizable record of stored produce."""

    receipt_id: str
    state_id: str
    warehouse_id: str
    waybill_number: str = Field(description="delivering consignment")
    depositor_id: str
    produce_type: ProduceType
    quantity_kg: float = Field(gt=0)
    grade: Optional[str] = None
    storage_location: str
    collateralized: bool = False
    issued_at: datetime = Field(default_factory=utcnow)


class VerificationResult(BaseModel):
    """Response of the QR verification endpoint."""

    waybill_number: str
    valid: bool
    status: Optional[WaybillStatus] = None
    detail: Optional[str] = None
