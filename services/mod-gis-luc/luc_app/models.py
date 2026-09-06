"""Domain models for the LUC valuation engine."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AuditStatus(str, enum.Enum):
    """Classification produced by the unassessed-property spatial join
    (geospatial/sedona/unassessed_property_join.sql — the CASE expression)."""

    UNREGISTERED_ENCROACHMENT = "UNREGISTERED_ENCROACHMENT"
    UNASSESSED_IMPROVEMENT = "UNASSESSED_IMPROVEMENT"
    COMPLIANT = "COMPLIANT"


class JoinFinding(BaseModel):
    """One row of the Sedona building-footprint ↔ cadastral-parcel join output."""

    building_footprint_id: str
    estimated_area_sqm: float = Field(gt=0)
    parcel_uin: Optional[str] = None
    owner_stin: Optional[str] = None
    assessed_annual_luc_kobo: Optional[int] = None
    audit_status: AuditStatus


class ParcelSnapshot(BaseModel):
    """Cadastral registry row snapshot supplied with a valuation run."""

    parcel_uin: str
    owner_stin: str
    land_use_type: str
    area_sqm: float = Field(gt=0)
    lga_id: str = ""
    relief_codes: list[str] = Field(default_factory=list)


class BillStatus(str, enum.Enum):
    ISSUED = "ISSUED"
    PROVISIONAL = "PROVISIONAL"  # encroachment bills pending regularisation
    VOID = "VOID"


class LUCBill(BaseModel):
    """A generated Land Use Charge bill tied to a parcel (or footprint)."""

    bill_id: UUID
    valuation_run_id: UUID
    tenant_state_id: str
    parcel_uin: Optional[str]
    building_footprint_id: Optional[str] = None
    owner_stin: Optional[str]
    land_use_type: str
    charge_area_sqm: float
    rate_kobo_per_sqm: int
    gross_amount_kobo: int
    relief_fraction: float
    net_amount_kobo: int
    audit_status: AuditStatus
    status: BillStatus
    assessment_year: int
    issued_at: datetime


class ValuationRunSummary(BaseModel):
    """Result of one valuation run (triggered via API or the event consumer)."""

    valuation_run_id: UUID
    tenant_state_id: str
    assessment_year: int
    parcels_assessed: int
    bills_generated: int
    findings_ingested: int
    compliant_findings: int
    unassessed_improvement_bills: int
    encroachment_provisional_bills: int
    total_billed_kobo: int
