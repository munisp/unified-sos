"""CI verification of the unassessed-property join classification logic.

Runs the production Sedona SQL's business logic (ST_Intersects join + CASE
classification) on the committed GeoJSON fixtures via the local shapely runner.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from local.unassessed_property_join import (
    AuditStatus,
    load_buildings,
    load_parcels,
    run_unassessed_property_join,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _run(state: str = "ogun"):
    buildings = load_buildings(FIXTURES / "building_footprints.geojson")
    parcels = load_parcels(FIXTURES / "cadastral_parcels.geojson")
    return run_unassessed_property_join(buildings, parcels, state)


def test_fixture_classification_matches_expected_statuses():
    rows = {r.building_footprint_id: r for r in _run("ogun")}

    enc = rows["BF-ENC-1"]
    assert enc.audit_status == AuditStatus.UNREGISTERED_ENCROACHMENT
    assert enc.parcel_uin is None and enc.owner_stin is None

    uai = rows["BF-UAI-1"]
    assert uai.audit_status == AuditStatus.UNASSESSED_IMPROVEMENT
    assert uai.parcel_uin == "OG-ABK-0001"
    assert uai.assessed_annual_luc_kobo == 0

    cmp_ = rows["BF-CMP-1"]
    assert cmp_.audit_status == AuditStatus.COMPLIANT
    assert cmp_.parcel_uin == "OG-ABK-0002"
    assert cmp_.owner_stin == "STIN-OG-0002"
    assert cmp_.assessed_annual_luc_kobo == 120000


def test_tenant_filtering_excludes_other_states():
    rows = _run("ogun")
    assert {r.building_footprint_id for r in rows} == {"BF-ENC-1", "BF-UAI-1", "BF-CMP-1"}
    # The lagos footprint shares geometry with an ogun parcel but is a
    # different tenant — it must not appear in the ogun run.
    lagos_rows = _run("lagos")
    assert [r.building_footprint_id for r in lagos_rows] == ["BF-LAG-1"]
    assert lagos_rows[0].audit_status == AuditStatus.UNREGISTERED_ENCROACHMENT


def test_parcel_only_rows_are_filtered_like_the_production_where_clause():
    # OG-ABK-0003 has no intersecting footprint. The production query's
    # WHERE b.tenant_state_id = 'ogun' drops such right-side-only FULL OUTER
    # JOIN rows; the local runner must do the same (one row per footprint).
    rows = _run("ogun")
    assert all(r.building_footprint_id for r in rows)
    assert not any(r.parcel_uin == "OG-ABK-0003" for r in rows)
