"""Domain: CAD incident intake, unit registry, geofenced dispatch, trust fund.

Ratification-independent modules only; gated capabilities live behind
``gate.RatificationGate`` in the API layer.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class CrossTenantError(ValueError):
    """Tenant-isolation violation — mapped to HTTP 403 at the API layer

    (fail-closed, matching mod-ppp-investment TenantIsolationError)."""


#: Approximate state geofence bounding boxes (min_lat, min_lon, max_lat, max_lon)
#: for the six tenant states — reference-grade, production uses PostGIS/Sedona.
STATE_GEOFENCES: dict[str, tuple[float, float, float, float]] = {
    "lagos": (6.35, 2.65, 6.75, 4.35),
    "ogun": (6.30, 2.65, 7.35, 4.60),
    "osun": (7.05, 4.00, 8.10, 5.10),
    "benue": (6.35, 7.50, 8.20, 10.00),
    "nasarawa": (7.45, 7.10, 9.30, 9.60),
    "taraba": (6.40, 9.40, 9.60, 11.60),
}


class IncidentStatus(str, Enum):
    OPEN = "open"
    DISPATCHED = "dispatched"
    CLOSED = "closed"


class Incident(BaseModel):
    incident_id: str
    tenant_state_id: str
    agency: str  # e.g. AMOTEKUN | SO-SAFE | BSCPG | LNSC | NPF
    category: str  # e.g. robbery | medical | fire | disturbance
    latitude: float
    longitude: float
    status: IncidentStatus
    reported_at: str


class Unit(BaseModel):
    unit_id: str
    tenant_state_id: str
    agency: str
    call_sign: str
    personnel_count: int = Field(..., ge=0)
    biometric_enrolled: bool  # biometric ghost-worker control (payroll integrity)
    registered_at: str


class DispatchEvent(BaseModel):
    event_id: str
    incident_id: str
    unit_id: str
    latitude: float
    longitude: float
    dispatched_at: str
    geofence_verified: bool


class Donation(BaseModel):
    donation_id: str
    tenant_state_id: str
    donor_ref: str  # organization/public figure reference, publicly auditable
    amount_kobo: int = Field(..., gt=0)
    received_at: str


class Disbursement(BaseModel):
    disbursement_id: str
    tenant_state_id: str
    purpose: str
    amount_kobo: int = Field(..., gt=0)
    disbursed_at: str


class CadStore:
    """Thread-safe in-memory store. Production: PostGIS + Kafka + TigerBeetle
    trust-fund ledger (donations in, disbursements out, publicly auditable)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.incidents: dict[str, Incident] = {}
        self.units: dict[str, Unit] = {}
        self.dispatch_log: list[DispatchEvent] = []
        self.donations: list[Donation] = []
        self.disbursements: list[Disbursement] = []

    # --- incident intake ----------------------------------------------------
    def intake_incident(self, tenant_state_id: str, agency: str, category: str,
                        latitude: float, longitude: float) -> Incident:
        if not self.in_geofence(tenant_state_id, latitude, longitude):
            raise ValueError(
                f"incident location ({latitude}, {longitude}) outside the "
                f"'{tenant_state_id}' operational geofence"
            )
        inc = Incident(incident_id=_id("inc"), tenant_state_id=tenant_state_id,
                       agency=agency, category=category, latitude=latitude,
                       longitude=longitude, status=IncidentStatus.OPEN,
                       reported_at=_now())
        with self._lock:
            self.incidents[inc.incident_id] = inc
        return inc

    # --- unit registry -------------------------------------------------------
    def register_unit(self, tenant_state_id: str, agency: str, call_sign: str,
                      personnel_count: int, biometric_enrolled: bool) -> Unit:
        unit = Unit(unit_id=_id("unit"), tenant_state_id=tenant_state_id, agency=agency,
                    call_sign=call_sign, personnel_count=personnel_count,
                    biometric_enrolled=biometric_enrolled, registered_at=_now())
        with self._lock:
            self.units[unit.unit_id] = unit
        return unit

    # --- dispatch -------------------------------------------------------------
    @staticmethod
    def in_geofence(state: str, lat: float, lon: float) -> bool:
        box = STATE_GEOFENCES.get(state)
        if box is None:
            return False
        return box[0] <= lat <= box[2] and box[1] <= lon <= box[3]

    def dispatch(self, incident_id: str, unit_id: str,
                 latitude: float, longitude: float) -> DispatchEvent:
        with self._lock:
            inc = self.incidents.get(incident_id)
            if inc is None:
                raise KeyError(f"incident '{incident_id}' not found")
            unit = self.units.get(unit_id)
            if unit is None:
                raise KeyError(f"unit '{unit_id}' not found")
            if unit.tenant_state_id != inc.tenant_state_id:
                raise CrossTenantError("cross-tenant dispatch is prohibited")
            if not self.in_geofence(inc.tenant_state_id, latitude, longitude):
                raise ValueError(
                    f"dispatch location outside the '{inc.tenant_state_id}' geofence"
                )
            event = DispatchEvent(event_id=_id("dsp"), incident_id=incident_id,
                                  unit_id=unit_id, latitude=latitude, longitude=longitude,
                                  dispatched_at=_now(), geofence_verified=True)
            inc.status = IncidentStatus.DISPATCHED
            self.dispatch_log.append(event)
            return event

    # --- trust fund ------------------------------------------------------------
    def record_donation(self, tenant_state_id: str, donor_ref: str, amount_kobo: int) -> Donation:
        donation = Donation(donation_id=_id("don"), tenant_state_id=tenant_state_id,
                            donor_ref=donor_ref, amount_kobo=amount_kobo, received_at=_now())
        with self._lock:
            self.donations.append(donation)
        return donation

    def record_disbursement(self, tenant_state_id: str, purpose: str,
                            amount_kobo: int) -> Disbursement:
        """Disburse from the trust fund; cannot exceed the fund balance."""
        with self._lock:
            balance = self.fund_balance(tenant_state_id)
            if amount_kobo > balance:
                raise ValueError(
                    f"disbursement {amount_kobo} kobo exceeds trust-fund balance {balance} kobo"
                )
            disb = Disbursement(disbursement_id=_id("dsb"), tenant_state_id=tenant_state_id,
                                purpose=purpose, amount_kobo=amount_kobo, disbursed_at=_now())
            self.disbursements.append(disb)
            return disb

    def fund_balance(self, tenant_state_id: str) -> int:
        donated = sum(d.amount_kobo for d in self.donations
                      if d.tenant_state_id == tenant_state_id)
        spent = sum(d.amount_kobo for d in self.disbursements
                    if d.tenant_state_id == tenant_state_id)
        return donated - spent

    def public_audit_feed(self, tenant_state_id: str) -> dict:
        """Public transparency feed: donations in, disbursements out, balance."""
        donations = [d.model_dump() for d in self.donations
                     if d.tenant_state_id == tenant_state_id]
        disbursements = [d.model_dump() for d in self.disbursements
                         if d.tenant_state_id == tenant_state_id]
        return {
            "tenant_state_id": tenant_state_id,
            "balance_kobo": self.fund_balance(tenant_state_id),
            "donations": donations,
            "disbursements": disbursements,
        }
