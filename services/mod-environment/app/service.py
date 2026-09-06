"""Domain service: telemetry compliance, permits/levies, deforestation
surveillance, carbon registry, and EIA workflow (ENV-09).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from .domain import (
    ALERT_RESPONSE_SLA_HOURS,
    AlertStatus,
    CarbonCredit,
    CarbonProject,
    CarbonProjectStatus,
    ComplianceIncident,
    ComplianceLimit,
    ComplianceStatus,
    CreditStatus,
    DeforestationAlert,
    EIAApplication,
    EIAStatus,
    Medium,
    Parameter,
    Permit,
    PermitStatus,
    TelemetryEvaluation,
    TelemetryReading,
)
from .repository import (
    CarbonCreditRepository,
    CarbonProjectRepository,
    DeforestationAlertRepository,
    EIARepository,
    IncidentRepository,
    LimitRepository,
    NotFoundError,
    PermitRepository,
    TelemetryRepository,
)

TOPIC_DEFORESTATION_ALERT = "ng.sos.environment.deforestation_alert_raised"
TOPIC_COMPLIANCE_VIOLATION = "ng.sos.environment.compliance_violation_raised"


class InvalidTransition(ValueError):
    pass


#: Per-state compliance limits [DERIVED] seed — Lagos/Ogun industrial
#: air/effluent plus Taraba forestry-adjacent defaults. Production limits
#: come from the state's environmental policy pack, never per-state forks.
DEFAULT_LIMITS: List[ComplianceLimit] = [
    ComplianceLimit(
        tenant_state_id="lagos", medium=Medium.AIR, parameter=Parameter.PM2_5,
        warning_threshold=35.0, violation_threshold=50.0, unit="ug/m3",
    ),
    ComplianceLimit(
        tenant_state_id="lagos", medium=Medium.AIR, parameter=Parameter.NO2,
        warning_threshold=80.0, violation_threshold=100.0, unit="ug/m3",
    ),
    ComplianceLimit(
        tenant_state_id="lagos", medium=Medium.WATER, parameter=Parameter.BOD,
        warning_threshold=25.0, violation_threshold=30.0, unit="mg/L",
    ),
    ComplianceLimit(
        tenant_state_id="lagos", medium=Medium.WATER, parameter=Parameter.COD,
        warning_threshold=90.0, violation_threshold=120.0, unit="mg/L",
    ),
    ComplianceLimit(
        tenant_state_id="lagos", medium=Medium.WATER, parameter=Parameter.PH,
        warning_threshold=9.0, violation_threshold=9.5, unit="pH",
    ),
    ComplianceLimit(
        tenant_state_id="ogun", medium=Medium.AIR, parameter=Parameter.PM2_5,
        warning_threshold=35.0, violation_threshold=50.0, unit="ug/m3",
    ),
    ComplianceLimit(
        tenant_state_id="ogun", medium=Medium.WATER, parameter=Parameter.BOD,
        warning_threshold=25.0, violation_threshold=30.0, unit="mg/L",
    ),
    ComplianceLimit(
        tenant_state_id="taraba", medium=Medium.WATER, parameter=Parameter.BOD,
        warning_threshold=20.0, violation_threshold=25.0, unit="mg/L",
    ),
    ComplianceLimit(
        tenant_state_id="taraba", medium=Medium.NOISE, parameter=Parameter.PM2_5,
        warning_threshold=40.0, violation_threshold=55.0, unit="dB-proxy",
    ),
]

#: Fine schedule [DERIVED]: base fine per violation (kobo) multiplied by a
#: per-state multiplier. Lagos/Ogun industrial deterrence is highest.
BASE_VIOLATION_FINE_KOBO = 5_000_000_00  # N5,000,000
STATE_FINE_MULTIPLIERS: Dict[str, float] = {
    "lagos": 2.0,
    "ogun": 1.5,
    "osun": 1.0,
    "benue": 1.0,
    "nasarawa": 1.0,
    "taraba": 1.0,
}

#: Carbon brokerage fee [DERIVED] conservative default: 3% of credit price,
#: overridable per state via config.
DEFAULT_BROKERAGE_BPS = 300  # 3.00%
STATE_BROKERAGE_BPS: Dict[str, int] = {}

#: EIA status workflow.
_EIA_TRANSITIONS: Dict[EIAStatus, List[EIAStatus]] = {
    EIAStatus.SUBMITTED: [EIAStatus.SCREENING, EIAStatus.REJECTED],
    EIAStatus.SCREENING: [EIAStatus.PUBLIC_COMMENT, EIAStatus.REJECTED],
    EIAStatus.PUBLIC_COMMENT: [EIAStatus.APPROVED, EIAStatus.REJECTED],
    EIAStatus.APPROVED: [],
    EIAStatus.REJECTED: [],
}

_PERMIT_TERMINAL = {PermitStatus.SUSPENDED, PermitStatus.EXPIRED}

_CREDIT_TERMINAL = CreditStatus.RETIRED


class EnvironmentService:
    def __init__(self, bus=None, limits: Optional[List[ComplianceLimit]] = None,
                 fine_multipliers: Optional[Dict[str, float]] = None,
                 brokerage_bps: Optional[Dict[str, int]] = None) -> None:
        self.telemetry = TelemetryRepository()
        self.incidents = IncidentRepository()
        self.limits = LimitRepository(limits or DEFAULT_LIMITS)
        self.permits = PermitRepository()
        self.alerts = DeforestationAlertRepository()
        self.projects = CarbonProjectRepository()
        self.credits = CarbonCreditRepository()
        self.eias = EIARepository()
        self.bus = bus  # optional event bus seam (Kafka/Fluvio in prod) [GAP]
        self.fine_multipliers = fine_multipliers or STATE_FINE_MULTIPLIERS
        self.brokerage_bps = brokerage_bps or STATE_BROKERAGE_BPS

    # -- telemetry & compliance ------------------------------------------------

    def ingest_telemetry(self, reading: TelemetryReading) -> TelemetryEvaluation:
        """Evaluate a reading against the state's limit table; violations raise
        a ComplianceIncident with a deterministic fine estimate."""
        self.telemetry.add(reading)
        limit = self.limits.find(
            reading.tenant_state_id, reading.medium, reading.parameter
        )
        if limit is None:
            return TelemetryEvaluation(reading=reading, status=ComplianceStatus.COMPLIANT)
        if reading.value >= limit.violation_threshold:
            multiplier = self.fine_multipliers.get(reading.tenant_state_id, 1.0)
            incident = ComplianceIncident(
                tenant_state_id=reading.tenant_state_id,
                facility_id=reading.facility_id,
                reading_id=reading.reading_id,
                medium=reading.medium,
                parameter=reading.parameter,
                measured_value=reading.value,
                violation_threshold=limit.violation_threshold,
                base_fine_kobo=BASE_VIOLATION_FINE_KOBO,
                fine_multiplier=multiplier,
                fine_estimate_kobo=int(BASE_VIOLATION_FINE_KOBO * multiplier),
            )
            self.incidents.add(incident)
            if self.bus is not None:
                self.bus.publish(TOPIC_COMPLIANCE_VIOLATION, incident)
            return TelemetryEvaluation(
                reading=reading, status=ComplianceStatus.VIOLATION, incident=incident
            )
        if reading.value >= limit.warning_threshold:
            return TelemetryEvaluation(reading=reading, status=ComplianceStatus.WARNING)
        return TelemetryEvaluation(reading=reading, status=ComplianceStatus.COMPLIANT)

    def facility_compliance(self, tenant_state_id: str, facility_id: str) -> dict:
        readings = self.telemetry.list_for_facility(tenant_state_id, facility_id)
        incidents = self.incidents.list_for_facility(tenant_state_id, facility_id)
        statuses = [
            self.ingest_status(r) for r in readings
        ]
        violations = sum(1 for s in statuses if s == ComplianceStatus.VIOLATION)
        warnings = sum(1 for s in statuses if s == ComplianceStatus.WARNING)
        evaluated = len(readings)
        return {
            "tenant_state_id": tenant_state_id,
            "facility_id": facility_id,
            "readings_evaluated": evaluated,
            "violations": violations,
            "warnings": warnings,
            "compliance_rate": (
                round((evaluated - violations) / evaluated, 4) if evaluated else 1.0
            ),
            "total_fines_kobo": sum(i.fine_estimate_kobo for i in incidents),
            "incident_ids": [i.incident_id for i in incidents],
        }

    def ingest_status(self, reading: TelemetryReading) -> ComplianceStatus:
        limit = self.limits.find(
            reading.tenant_state_id, reading.medium, reading.parameter
        )
        if limit is None or reading.value < limit.warning_threshold:
            return ComplianceStatus.COMPLIANT
        if reading.value >= limit.violation_threshold:
            return ComplianceStatus.VIOLATION
        return ComplianceStatus.WARNING

    # -- permits & levies --------------------------------------------------------

    def create_permit(self, permit: Permit) -> Permit:
        return self.permits.add(permit)

    def activate_permit(self, tenant_state_id: str, permit_id: str) -> Permit:
        permit = self.permits.get(permit_id, tenant_state_id)
        if permit.status != PermitStatus.DRAFT:
            raise InvalidTransition(
                f"permit {permit_id} is {permit.status.value}; only DRAFT can activate"
            )
        permit.status = PermitStatus.ACTIVE
        return permit

    # -- deforestation surveillance ---------------------------------------------

    def raise_deforestation_alert(self, alert: DeforestationAlert) -> DeforestationAlert:
        """Ingest an NDVI alert from the lakehouse job and compute the 4-hour
        enforcement response SLA deadline (ENV-09 KPI)."""
        alert.sla_deadline = alert.detected_at + timedelta(hours=ALERT_RESPONSE_SLA_HOURS)
        self.alerts.add(alert)
        if self.bus is not None:
            self.bus.publish(TOPIC_DEFORESTATION_ALERT, alert)
        return alert

    def dispatch_alert(self, tenant_state_id: str, alert_id: str) -> DeforestationAlert:
        """Dispatch an enforcement ticket reference for SEC-10 ranger tasking."""
        alert = self.alerts.get(alert_id, tenant_state_id)
        if alert.status != AlertStatus.RAISED:
            raise InvalidTransition(
                f"alert {alert_id} is {alert.status.value}; only RAISED can dispatch"
            )
        alert.status = AlertStatus.DISPATCHED
        alert.enforcement_ticket_ref = f"SEC10-{uuid4().hex[:8].upper()}"
        return alert

    # -- carbon registry -----------------------------------------------------------

    def register_project(self, project: CarbonProject) -> CarbonProject:
        return self.projects.add(project)

    def create_credit(self, credit: CarbonCredit) -> CarbonCredit:
        if self.credits.serial_exists(credit.tenant_state_id, credit.serial):
            raise ValueError(f"serial {credit.serial!r} already registered for tenant")
        # project must exist within the same tenant
        self.projects.get(credit.project_id, credit.tenant_state_id)
        return self.credits.add(credit)

    def issue_credit(self, tenant_state_id: str, credit_id: str) -> CarbonCredit:
        credit = self.credits.get(credit_id, tenant_state_id)
        if credit.status != CreditStatus.REGISTERED:
            raise InvalidTransition(
                f"credit {credit_id} is {credit.status.value}; cannot issue"
            )
        credit.status = CreditStatus.ISSUED
        return credit

    def brokerage_fee_kobo(self, credit: CarbonCredit) -> int:
        bps = self.brokerage_bps.get(credit.tenant_state_id, DEFAULT_BROKERAGE_BPS)
        return credit.price_kobo * bps // 10_000

    def transfer_credit(
        self, tenant_state_id: str, credit_id: str, new_owner_id: str
    ) -> dict:
        """Transfer an issued credit; returns the credit plus the brokerage fee
        settlement lines (state CRF leg under account 3001)."""
        credit = self.credits.get(credit_id, tenant_state_id)
        if credit.status != CreditStatus.ISSUED:
            raise InvalidTransition(
                f"credit {credit_id} is {credit.status.value}; only ISSUED can transfer"
            )
        credit.status = CreditStatus.TRANSFERRED
        credit.owner_id = new_owner_id
        fee = self.brokerage_fee_kobo(credit)
        return {
            "credit": credit,
            "brokerage_fee_kobo": fee,
            "settlement_lines": [
                {
                    "ledger_account_code": "3001",  # state CRF/TSA
                    "amount_kobo": credit.price_kobo - fee,
                },
                {
                    "ledger_account_code": "3001",
                    "memo": "carbon brokerage fee",
                    "amount_kobo": fee,
                },
            ],
        }

    def retire_credit(self, tenant_state_id: str, credit_id: str) -> CarbonCredit:
        """Retirement is terminal: retired credits cannot move again."""
        credit = self.credits.get(credit_id, tenant_state_id)
        if credit.status == CreditStatus.RETIRED:
            raise InvalidTransition(f"credit {credit_id} already RETIRED (terminal)")
        credit.status = CreditStatus.RETIRED
        return credit

    # -- EIA workflow ---------------------------------------------------------------

    def submit_eia(self, application: EIAApplication) -> EIAApplication:
        if application.temporal_workflow_ref is None:
            application.temporal_workflow_ref = f"WF-EIA-{uuid4().hex[:10]}"
        return self.eias.add(application)

    def advance_eia(
        self,
        tenant_state_id: str,
        application_id: str,
        target: EIAStatus,
        decision_reason: Optional[str] = None,
    ) -> EIAApplication:
        app = self.eias.get(application_id, tenant_state_id)
        allowed = _EIA_TRANSITIONS[app.status]
        if target not in allowed:
            raise InvalidTransition(
                f"EIA {application_id} is {app.status.value}; cannot move to {target.value}"
            )
        if target in (EIAStatus.APPROVED, EIAStatus.REJECTED) and not decision_reason:
            raise InvalidTransition("decision requires a decision_reason")
        app.status = target
        app.decision_reason = decision_reason
        return app
