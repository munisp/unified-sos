"""Tests for the unassessed-property join consumer and valuation runs."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from luc_app.ingestion import process_finding, run_valuation
from luc_app.models import AuditStatus, BillStatus, JoinFinding, ParcelSnapshot
from luc_app.tariffs import tariff_for_state

OGUN = tariff_for_state("ogun")
YEAR = 2026

REGISTRY = {
    "OG-ABK-0001": ParcelSnapshot(
        parcel_uin="OG-ABK-0001", owner_stin="STIN-1",
        land_use_type="RESIDENTIAL", area_sqm=1_000.0,
    ),
    "OG-ABK-0002": ParcelSnapshot(
        parcel_uin="OG-ABK-0002", owner_stin="STIN-2",
        land_use_type="COMMERCIAL", area_sqm=500.0,
    ),
}


def _finding(status: AuditStatus, **kw) -> JoinFinding:
    base = dict(building_footprint_id="BF-1", estimated_area_sqm=120.0, audit_status=status)
    base.update(kw)
    return JoinFinding(**base)


class TestFindingConsumer:
    def test_compliant_produces_no_bill(self):
        f = _finding(AuditStatus.COMPLIANT, parcel_uin="OG-ABK-0001", assessed_annual_luc_kobo=120_000)
        assert process_finding(OGUN, f, registry=REGISTRY, valuation_run_id=__import__("uuid").uuid4(), assessment_year=YEAR) is None

    def test_unassessed_improvement_billed_from_registry(self):
        f = _finding(AuditStatus.UNASSESSED_IMPROVEMENT, parcel_uin="OG-ABK-0001", owner_stin="STIN-1", assessed_annual_luc_kobo=0)
        bill = process_finding(OGUN, f, registry=REGISTRY, valuation_run_id=__import__("uuid").uuid4(), assessment_year=YEAR)
        assert bill is not None
        assert bill.status == BillStatus.ISSUED
        assert bill.parcel_uin == "OG-ABK-0001"
        assert bill.land_use_type == "RESIDENTIAL"
        assert bill.charge_area_sqm == 1_000.0  # registry parcel area, not footprint
        # 1,000 m² × 120 kobo = 120k < ₦5,000 statutory floor -> floor applies.
        assert bill.net_amount_kobo == OGUN.minimum_bill_kobo

    def test_unregistered_encroachment_gets_provisional_bill(self):
        f = _finding(AuditStatus.UNREGISTERED_ENCROACHMENT, estimated_area_sqm=250.0)
        bill = process_finding(OGUN, f, registry=REGISTRY, valuation_run_id=__import__("uuid").uuid4(), assessment_year=YEAR)
        assert bill is not None
        assert bill.status == BillStatus.PROVISIONAL
        assert bill.parcel_uin is None
        # Default land use at footprint area, no reliefs until regularisation.
        # 250 m² × 120 kobo = 30k < ₦5,000 statutory floor -> floor applies.
        assert bill.charge_area_sqm == 250.0
        assert bill.net_amount_kobo == OGUN.minimum_bill_kobo

    def test_improvement_with_missing_parcel_falls_back_to_provisional(self):
        f = _finding(AuditStatus.UNASSESSED_IMPROVEMENT, parcel_uin="GHOST-1")
        bill = process_finding(OGUN, f, registry=REGISTRY, valuation_run_id=__import__("uuid").uuid4(), assessment_year=YEAR)
        assert bill is not None
        assert bill.status == BillStatus.PROVISIONAL


class TestValuationRun:
    def test_run_bills_registry_and_ingests_findings(self):
        parcels = list(REGISTRY.values())
        findings = [
            _finding(AuditStatus.COMPLIANT, parcel_uin="OG-ABK-0001", assessed_annual_luc_kobo=120_000),
            _finding(AuditStatus.UNASSESSED_IMPROVEMENT, parcel_uin="OG-ABK-0002", owner_stin="STIN-2", assessed_annual_luc_kobo=0),
            _finding(AuditStatus.UNREGISTERED_ENCROACHMENT, building_footprint_id="BF-9", estimated_area_sqm=300.0),
        ]
        summary, bills = run_valuation(OGUN, parcels=parcels, findings=findings, assessment_year=YEAR)

        # OG-ABK-0001 is compliant (no recurring bill, finding confirms it);
        # OG-ABK-0002 billed via finding; encroachment billed provisionally.
        assert summary.parcels_assessed == 2
        assert summary.compliant_findings == 1
        assert summary.unassessed_improvement_bills == 1
        assert summary.encroachment_provisional_bills == 1
        assert summary.bills_generated == 2
        assert summary.total_billed_kobo == sum(b.net_amount_kobo for b in bills)
        improvement = next(b for b in bills if b.parcel_uin == "OG-ABK-0002")
        # 500 m² × 400 kobo (COMMERCIAL) = 200k < ₦5,000 floor -> floor applies.
        assert improvement.net_amount_kobo == OGUN.minimum_bill_kobo

    def test_run_without_findings_bills_every_parcel(self):
        summary, bills = run_valuation(OGUN, parcels=list(REGISTRY.values()), findings=[], assessment_year=YEAR)
        assert summary.bills_generated == 2
        assert {b.parcel_uin for b in bills} == set(REGISTRY)
