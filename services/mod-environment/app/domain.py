"""Domain models for mod-environment — ENV-09 Environmental Protection,
Carbon Registry & Industrial Emissions.

Money is integer kobo. Every tenant-owned object carries ``tenant_state_id``.
Provenance: telemetry limits and fine multipliers are config-like seed data
[DERIVED] until state policy packs provide authoritative values.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

STATE_IDS = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")

#: 4-hour enforcement response SLA for deforestation alerts (ENV-09 KPI).
ALERT_RESPONSE_SLA_HOURS = 4

#: Ledger account codes (ledger/chart-of-accounts.md).
LEDGER_ACCOUNT_STATE_CRF = "3001"          # state CRF/TSA
LEDGER_ACCOUNT_ESCROW = "2099"             # PPP concessionaire escrow


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class Medium(str, enum.Enum):
    AIR = "AIR"
    WATER = "WATER"
    NOISE = "NOISE"


class Parameter(str, enum.Enum):
    PM2_5 = "PM2_5"
    NO2 = "NO2"
    BOD = "BOD"
    COD = "COD"
    PH = "PH"


class ComplianceStatus(str, enum.Enum):
    COMPLIANT = "COMPLIANT"
    WARNING = "WARNING"
    VIOLATION = "VIOLATION"


class TelemetryReading(BaseModel):
    reading_id: str = Field(default_factory=lambda: _id("TELE"))
    tenant_state_id: str = Field(description="tenant: " + " | ".join(STATE_IDS))
    facility_id: str
    sensor_id: str
    medium: Medium
    parameter: Parameter
    value: float
    unit: str
    measured_at: datetime = Field(default_factory=utcnow)


class ComplianceLimit(BaseModel):
    """Per-state regulatory limit for a medium+parameter."""

    tenant_state_id: str
    medium: Medium
    parameter: Parameter
    warning_threshold: float
    violation_threshold: float
    unit: str


class ComplianceIncident(BaseModel):
    incident_id: str = Field(default_factory=lambda: _id("INC"))
    tenant_state_id: str
    facility_id: str
    reading_id: str
    medium: Medium
    parameter: Parameter
    measured_value: float
    violation_threshold: float
    base_fine_kobo: int = Field(ge=0)
    fine_multiplier: float = Field(gt=0)
    fine_estimate_kobo: int = Field(ge=0)
    ledger_account_code: str = LEDGER_ACCOUNT_STATE_CRF
    status: str = "OPEN"
    raised_at: datetime = Field(default_factory=utcnow)


class TelemetryEvaluation(BaseModel):
    reading: TelemetryReading
    status: ComplianceStatus
    incident: Optional[ComplianceIncident] = None


class PermitType(str, enum.Enum):
    EFFLUENT_DISCHARGE = "EFFLUENT_DISCHARGE"
    TIMBER_LOGGING = "TIMBER_LOGGING"


class PermitStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"


class Permit(BaseModel):
    permit_id: str = Field(default_factory=lambda: _id("PERMIT"))
    tenant_state_id: str
    permit_type: PermitType
    holder_id: str
    facility_id: Optional[str] = None
    fee_kobo: int = Field(ge=0)
    status: PermitStatus = PermitStatus.DRAFT
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: Optional[datetime] = None


class AlertSource(str, enum.Enum):
    SENTINEL2 = "SENTINEL2"
    LANDSAT = "LANDSAT"


class AlertStatus(str, enum.Enum):
    RAISED = "RAISED"
    DISPATCHED = "DISPATCHED"
    RESOLVED = "RESOLVED"


class DeforestationAlert(BaseModel):
    """Raised from the lakehouse/Sedona NDVI change-detection job."""

    alert_id: str = Field(default_factory=lambda: _id("DEFOR"))
    tenant_state_id: str
    polygon: dict = Field(description="GeoJSON-like polygon dict")
    h3_cells: List[str] = Field(default_factory=list)
    ndvi_delta: float = Field(ge=0, le=1)
    area_hectares: float = Field(gt=0)
    source: AlertSource
    status: AlertStatus = AlertStatus.RAISED
    detected_at: datetime = Field(default_factory=utcnow)
    sla_deadline: Optional[datetime] = None
    enforcement_ticket_ref: Optional[str] = None  # SEC-10 ticket reference


class CarbonProjectStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    ISSUED = "ISSUED"
    TRANSFERRED = "TRANSFERRED"
    RETIRED = "RETIRED"


class CarbonProject(BaseModel):
    project_id: str = Field(default_factory=lambda: _id("CPROJ"))
    tenant_state_id: str
    name: str
    boundary: dict = Field(description="GeoJSON-like project boundary")
    methodology: str = "REDD+"
    status: CarbonProjectStatus = CarbonProjectStatus.REGISTERED
    registered_at: datetime = Field(default_factory=utcnow)


class CreditStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    ISSUED = "ISSUED"
    TRANSFERRED = "TRANSFERRED"
    RETIRED = "RETIRED"


class CarbonCredit(BaseModel):
    credit_id: str = Field(default_factory=lambda: _id("CRDT"))
    tenant_state_id: str
    project_id: str
    serial: str = Field(description="unique registry serial, e.g. NG-TAR-2026-0001")
    vintage: int = Field(ge=2000)
    quantity_tco2e: float = Field(gt=0)
    price_kobo: int = Field(default=0, ge=0)
    status: CreditStatus = CreditStatus.REGISTERED
    owner_id: str


class EIAStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    SCREENING = "SCREENING"
    PUBLIC_COMMENT = "PUBLIC_COMMENT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EIAApplication(BaseModel):
    application_id: str = Field(default_factory=lambda: _id("EIA"))
    tenant_state_id: str
    project_name: str
    facility_id: Optional[str] = None
    category: str = Field(description="e.g. CATEGORY_1 | CATEGORY_2 | CATEGORY_3")
    documents: List[str] = Field(default_factory=list)
    status: EIAStatus = EIAStatus.SUBMITTED
    decision_reason: Optional[str] = None
    temporal_workflow_ref: Optional[str] = None  # durable workflow seam [GAP]
    submitted_at: datetime = Field(default_factory=utcnow)
