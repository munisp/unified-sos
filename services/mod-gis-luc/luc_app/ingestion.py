"""Consumer for the unassessed-property spatial join output.

Input rows are the result of geospatial/sedona/unassessed_property_join.sql
(building footprints FULL OUTER JOIN cadastral parcels on ST_Intersects),
classified as UNREGISTERED_ENCROACHMENT / UNASSESSED_IMPROVEMENT / COMPLIANT
and published on ``ng.sos.gis.unassessed_property_discovered`` (AsyncAPI:
contracts/asyncapi/platform-events.yaml). In production this module is invoked
from a Kafka consumer; here it is a plain function so the FastAPI endpoint and
tests drive it directly.

Assessment policy:

* **COMPLIANT** — footprint already sits on an assessed parcel; no bill.
* **UNASSESSED_IMPROVEMENT** — parcel exists in the registry but carries a zero
  LUC assessment; generate a full bill from the registry's parcel area, land
  use and statutory reliefs.
* **UNREGISTERED_ENCROACHMENT** — footprint intersects no cadastral parcel;
  generate a PROVISIONAL bill on the footprint area at the state's default
  land use, flagged for regularisation/enforcement follow-up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from .calculator import compute_charge
from .models import (
    AuditStatus,
    BillStatus,
    JoinFinding,
    LUCBill,
    ParcelSnapshot,
    ValuationRunSummary,
)
from .tariffs import StateTariff


def generate_bill_for_parcel(
    tariff: StateTariff,
    parcel: ParcelSnapshot,
    *,
    valuation_run_id: UUID,
    audit_status: AuditStatus,
    assessment_year: int,
    status: BillStatus = BillStatus.ISSUED,
    building_footprint_id: str | None = None,
    now: datetime | None = None,
) -> LUCBill:
    """Generate one LUC bill for a registry parcel under the state tariff."""

    charge = compute_charge(
        tariff,
        land_use_type=parcel.land_use_type,
        area_sqm=parcel.area_sqm,
        relief_codes=parcel.relief_codes,
    )
    return LUCBill(
        bill_id=uuid4(),
        valuation_run_id=valuation_run_id,
        tenant_state_id=tariff.tenant_state_id,
        parcel_uin=parcel.parcel_uin,
        building_footprint_id=building_footprint_id,
        owner_stin=parcel.owner_stin,
        land_use_type=parcel.land_use_type,
        charge_area_sqm=charge.charge_area_sqm,
        rate_kobo_per_sqm=charge.rate_kobo_per_sqm,
        gross_amount_kobo=charge.gross_amount_kobo,
        relief_fraction=charge.relief_fraction,
        net_amount_kobo=charge.net_amount_kobo,
        audit_status=audit_status,
        status=status,
        assessment_year=assessment_year,
        issued_at=now or datetime.now(timezone.utc),
    )


def process_finding(
    tariff: StateTariff,
    finding: JoinFinding,
    *,
    registry: dict[str, ParcelSnapshot],
    valuation_run_id: UUID,
    assessment_year: int,
    now: datetime | None = None,
) -> LUCBill | None:
    """Convert one classified join row into a bill (or None if COMPLIANT).

    :param registry: parcel_uin -> ParcelSnapshot of the tenant's cadastral
        registry (supplied by the valuation run caller / mod-gis-lands CDC).
    """

    if finding.audit_status == AuditStatus.COMPLIANT:
        return None

    if finding.audit_status == AuditStatus.UNASSESSED_IMPROVEMENT:
        parcel = registry.get(finding.parcel_uin or "")
        if parcel is None:
            # Parcel referenced by the join but absent from the snapshot:
            # fall back to a provisional footprint-based assessment rather
            # than silently dropping revenue.
            return _provisional_bill(
                tariff, finding, valuation_run_id, assessment_year, now,
                note_owner=finding.owner_stin,
            )
        return generate_bill_for_parcel(
            tariff, parcel,
            valuation_run_id=valuation_run_id,
            audit_status=finding.audit_status,
            assessment_year=assessment_year,
            building_footprint_id=finding.building_footprint_id,
            now=now,
        )

    # UNREGISTERED_ENCROACHMENT
    return _provisional_bill(
        tariff, finding, valuation_run_id, assessment_year, now,
        note_owner=finding.owner_stin,
    )


def _provisional_bill(
    tariff: StateTariff,
    finding: JoinFinding,
    valuation_run_id: UUID,
    assessment_year: int,
    now: datetime | None,
    note_owner: str | None,
) -> LUCBill:
    """Footprint-based PROVISIONAL bill at the state's default land use rate.

    No statutory reliefs apply until the occupant regularises title and the
    land use is confirmed by the lands registry.
    """
    charge = compute_charge(
        tariff,
        land_use_type=tariff.default_land_use_type,
        area_sqm=finding.estimated_area_sqm,
    )
    return LUCBill(
        bill_id=uuid4(),
        valuation_run_id=valuation_run_id,
        tenant_state_id=tariff.tenant_state_id,
        parcel_uin=finding.parcel_uin,
        building_footprint_id=finding.building_footprint_id,
        owner_stin=note_owner,
        land_use_type=tariff.default_land_use_type,
        charge_area_sqm=charge.charge_area_sqm,
        rate_kobo_per_sqm=charge.rate_kobo_per_sqm,
        gross_amount_kobo=charge.gross_amount_kobo,
        relief_fraction=charge.relief_fraction,
        net_amount_kobo=charge.net_amount_kobo,
        audit_status=AuditStatus.UNREGISTERED_ENCROACHMENT,
        status=BillStatus.PROVISIONAL,
        assessment_year=assessment_year,
        issued_at=now or datetime.now(timezone.utc),
    )


def run_valuation(
    tariff: StateTariff,
    *,
    parcels: list[ParcelSnapshot],
    findings: list[JoinFinding],
    assessment_year: int,
    valuation_run_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[ValuationRunSummary, list[LUCBill]]:
    """Execute one full valuation run for a state tenant.

    1. Bill every registry parcel whose uin is *not* already covered by a
       finding (policy-driven recurring annual billing).
    2. Ingest the classified join findings (consumer role).
    """

    run_id = valuation_run_id or uuid4()
    bills: list[LUCBill] = []
    registry = {p.parcel_uin: p for p in parcels}

    # Recurring annual bills for parcels untouched by this join output.
    finding_uins = {f.parcel_uin for f in findings if f.parcel_uin}
    for parcel in parcels:
        if parcel.parcel_uin in finding_uins:
            continue  # handled (or confirmed compliant) via findings below
        bills.append(
            generate_bill_for_parcel(
                tariff, parcel,
                valuation_run_id=run_id,
                audit_status=AuditStatus.COMPLIANT,
                assessment_year=assessment_year,
                now=now,
            )
        )

    improvement_bills = encroachment_bills = compliant = 0
    for finding in findings:
        bill = process_finding(
            tariff, finding,
            registry=registry, valuation_run_id=run_id,
            assessment_year=assessment_year, now=now,
        )
        if finding.audit_status == AuditStatus.COMPLIANT:
            compliant += 1
        elif bill is not None and finding.audit_status == AuditStatus.UNASSESSED_IMPROVEMENT:
            improvement_bills += 1
            bills.append(bill)
        elif bill is not None:
            encroachment_bills += 1
            bills.append(bill)

    summary = ValuationRunSummary(
        valuation_run_id=run_id,
        tenant_state_id=tariff.tenant_state_id,
        assessment_year=assessment_year,
        parcels_assessed=len(parcels),
        bills_generated=len(bills),
        findings_ingested=len(findings),
        compliant_findings=compliant,
        unassessed_improvement_bills=improvement_bills,
        encroachment_provisional_bills=encroachment_bills,
        total_billed_kobo=sum(b.net_amount_kobo for b in bills),
    )
    return summary, bills
