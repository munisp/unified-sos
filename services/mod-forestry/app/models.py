"""Domain models for mod-forestry — RFID timber provenance & deforestation alerts."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, computed_field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimberSpecies(str, enum.Enum):
    ROSEWOOD = "ROSEWOOD"
    IROKO = "IROKO"
    MAHOGANY = "MAHOGANY"
    OBECHI = "OBECHI"
    TEAK = "TEAK"
    AFARA = "AFARA"


class TagStatus(str, enum.Enum):
    ISSUED = "ISSUED"          # tag registered, not yet affixed
    HARVESTED = "HARVESTED"    # affixed to a felled log at coupe
    IN_TRANSIT = "IN_TRANSIT"  # moving under a transit permit
    MILLED = "MILLED"          # received at mill, provenance closed
    SEIZED = "SEIZED"          # enforcement seizure (broken chain)


class TimberTag(BaseModel):
    """UHF RFID nail-tag bound to a single log."""

    tag_id: str = Field(description="UHF RFID EPC, e.g. RFID-TAR-000123")
    state_id: str
    species: TimberSpecies
    coupe_id: str = Field(description="licensed harvest compartment")
    licensee_id: str
    volume_m3: Optional[float] = Field(default=None, ge=0)
    status: TagStatus = TagStatus.ISSUED
    issued_at: datetime = Field(default_factory=utcnow)


class ProvenanceEvent(BaseModel):
    """One hop in the log's chain of custody: harvest -> transit -> mill."""

    event_id: str
    tag_id: str
    stage: TagStatus
    actor_id: str = Field(description="ranger / hauler / mill operator ID")
    location: str
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    transit_permit_id: Optional[str] = None
    recorded_at: datetime = Field(default_factory=utcnow)


class DeforestationAlert(BaseModel):
    """Ingested from the Sedona NDVI change-detection job
    (geospatial/sedona/ndvi_change_detection.py).

    Acceptance: canopy disturbance > 0.5 ha flagged within 72 h of ingest.
    """

    alert_id: str
    state_id: str
    coupe_id: Optional[str] = None
    disturbed_area_ha: float = Field(gt=0)
    ndvi_drop: float = Field(ge=0, le=1, description="NDVI delta magnitude")
    gps_lat: float
    gps_lon: float
    detected_at: datetime
    ingested_at: datetime = Field(default_factory=utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def severity(self) -> str:
        if self.disturbed_area_ha >= 5.0:
            return "CRITICAL"
        if self.disturbed_area_ha > 0.5:
            return "ACTIONABLE"  # above the 0.5 ha enforcement threshold
        return "NOTICE"


class StumpageInvoice(BaseModel):
    """Levy/stumpage billing hook output for a harvest event."""

    invoice_id: str
    tag_id: str
    state_id: str
    species: TimberSpecies
    volume_m3: float = Field(ge=0)
    rate_kobo_per_m3: int = Field(ge=0)
    amount_kobo: int = Field(ge=0)
    transfer_code: int = 110  # natural-resource levy leg (chart-of-accounts)
    billed_at: datetime = Field(default_factory=utcnow)


class UntaggedTimberAlert(BaseModel):
    """Payload for ``ng.sos.forestry.untagged_timber_alert`` (AsyncAPI)."""

    state_id: str
    checkpoint_id: str
    vehicle_plate: str
    gps: dict
    timestamp: datetime = Field(default_factory=utcnow)
