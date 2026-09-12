"""Tests for the land-registry extensions: subdivision/merger, disputes,
chain-of-title history, title-risk scoring, and title-hash anchoring."""

import dataclasses
import hashlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.anchoring import FixtureAnchor, anchor_adapter_from_env
from lands_app.eventlog import CadastreEventLog
from lands_app.main import create_app
from lands_app.risk import (
    AdapterUnavailableError,
    FixtureTitleRiskScorer,
    HttpTitleRiskScorer,
    RiskFactor,
    risk_scorer_from_env,
)
from tests.helpers import (
    BASE_LAT,
    BASE_LON,
    geodesic_area_sqm,
    registration_body,
    square_geojson,
)

OGUN_H = {"X-State-Tenant": "ogun"}
LAGOS_H = {"X-State-Tenant": "lagos"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _rect_geojson(lon0: float, lat0: float, width: float, height: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon0, lat0], [lon0 + width, lat0],
            [lon0 + width, lat0 + height], [lon0, lat0 + height],
            [lon0, lat0],
        ]],
    }


def _register(client, uin: str, geojson: dict, tenant: str = "ogun") -> dict:
    resp = client.post(
        f"/api/v1/states/{tenant}/cadastre/parcels",
        json=registration_body(uin, geojson),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _settle_workflow(client, parcel: dict, tenant: str = "ogun") -> None:
    """Approve every titling stage so the workflow is no longer RUNNING."""
    wf = parcel["titling_workflow_id"]
    for _ in range(4):  # SURVEYOR → MINISTRY → AG → GOVERNOR (issuance)
        resp = client.post(
            f"/api/v1/states/{tenant}/cadastre/titling/{wf}/decisions",
            json={"approved": True, "actor": "test-approver"},
        )
        assert resp.status_code == 200, resp.text


def _issued_parcel(client, uin: str, geojson: dict, tenant: str = "ogun") -> dict:
    parcel = _register(client, uin, geojson, tenant)
    _settle_workflow(client, parcel, tenant)
    return parcel


def _halves(uin_prefix: str, lon0: float, lat0: float, size: float = 0.002) -> list[dict]:
    """Two side-by-side child specs exactly tiling a square parcel."""
    return [
        {"parcel_uin": f"{uin_prefix}-A", "boundary_geojson": _rect_geojson(lon0, lat0, size / 2, size)},
        {"parcel_uin": f"{uin_prefix}-B", "boundary_geojson": _rect_geojson(lon0 + size / 2, lat0, size / 2, size)},
    ]


def _open_dispute(client, parcel_id: str, tenant_h=OGUN_H, tenant="ogun") -> dict:
    resp = client.post(
        f"/api/v1/states/{tenant}/cadastre/parcels/{parcel_id}/disputes",
        json={"complainant": "STIN-OG-999", "grounds": "BOUNDARY",
              "description": "beacon encroachment"},
        headers=tenant_h,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestSubdivision:
    def test_happy_parent_superseded_children_lineage(self, client):
        parent = _issued_parcel(client, "OG-SUB-1", square_geojson(BASE_LON, BASE_LAT, 0.002))
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": _halves("OG-SUB-1", BASE_LON, BASE_LAT)},
            headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["parent"]["status"] == "SUPERSEDED"
        assert len(body["children"]) == 2
        for child in body["children"]:
            assert child["parent_parcel_ids"] == [parent["parcel_id"]]
            assert child["status"] == "REGISTERED"
        # Area conservation: children sum to parent area.
        assert abs(sum(c["area_sqm"] for c in body["children"])
                   - geodesic_area_sqm(square_geojson(BASE_LON, BASE_LAT, 0.002))) < 1.0

    def test_parent_superseded_not_deleted(self, client):
        parent = _issued_parcel(client, "OG-SUB-2", square_geojson(BASE_LON, BASE_LAT, 0.002))
        client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": _halves("OG-SUB-2", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        fetched = client.get(f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}")
        assert fetched.status_code == 200  # retained as chain-of-title evidence
        assert fetched.json()["status"] == "SUPERSEDED"

    def test_area_conservation_rejected_422(self, client):
        parent = _issued_parcel(client, "OG-SUB-3", square_geojson(BASE_LON, BASE_LAT, 0.002))
        children = [
            {"parcel_uin": "OG-SUB-3-A", "boundary_geojson": _rect_geojson(BASE_LON, BASE_LAT, 0.0012, 0.002)},
            {"parcel_uin": "OG-SUB-3-B", "boundary_geojson": _rect_geojson(BASE_LON + 0.0012, BASE_LAT, 0.0004, 0.002)},
        ]  # covers only 80% of the parent
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 422
        assert "area conservation" in resp.json()["detail"].lower()

    def test_within_tolerance_accepted(self, client):
        parent = _issued_parcel(client, "OG-SUB-4", square_geojson(BASE_LON, BASE_LAT, 0.002))
        children = [
            {"parcel_uin": "OG-SUB-4-A", "boundary_geojson": _rect_geojson(BASE_LON, BASE_LAT, 0.001, 0.002)},
            # second child 0.4% narrower than the exact half → inside 0.5% tolerance
            {"parcel_uin": "OG-SUB-4-B", "boundary_geojson": _rect_geojson(BASE_LON + 0.001, BASE_LAT, 0.000996, 0.002)},
        ]
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": children}, headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text

    def test_single_child_rejected_422(self, client):
        parent = _issued_parcel(client, "OG-SUB-5", square_geojson(BASE_LON, BASE_LAT, 0.002))
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": [{"parcel_uin": "OG-SUB-5-A",
                                "boundary_geojson": square_geojson(BASE_LON, BASE_LAT, 0.002)}]},
            headers=OGUN_H,
        )
        assert resp.status_code == 422

    def test_blocked_while_titling_workflow_running_409(self, client):
        parent = _register(client, "OG-SUB-6", square_geojson(BASE_LON, BASE_LAT, 0.002))
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": _halves("OG-SUB-6", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        assert resp.status_code == 409
        assert "RUNNING" in resp.json()["detail"]

    def test_blocked_by_open_dispute_409(self, client):
        parent = _issued_parcel(client, "OG-SUB-7", square_geojson(BASE_LON, BASE_LAT, 0.002))
        _open_dispute(client, parent["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": _halves("OG-SUB-7", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        assert resp.status_code == 409
        assert "dispute" in resp.json()["detail"].lower()

    def test_unknown_parcel_404(self, client):
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/00000000-0000-0000-0000-000000000000/subdivide",
            json={"children": _halves("OG-SUB-X", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        assert resp.status_code == 404


class TestMerger:
    def test_merge_happy_parents_superseded(self, client):
        p1 = _issued_parcel(client, "OG-MRG-1", square_geojson(BASE_LON, BASE_LAT, 0.001))
        p2 = _issued_parcel(client, "OG-MRG-2", square_geojson(BASE_LON + 0.001, BASE_LAT, 0.001))
        child_geo = _rect_geojson(BASE_LON, BASE_LAT, 0.002, 0.001)
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                  "child": {"parcel_uin": "OG-MRG-C", "boundary_geojson": child_geo}},
            headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert {p["status"] for p in body["parents"]} == {"SUPERSEDED"}
        assert set(body["child"]["parent_parcel_ids"]) == {p1["parcel_id"], p2["parcel_id"]}
        expected = geodesic_area_sqm(child_geo)
        assert abs(body["child"]["area_sqm"] - expected) < 1.0

    def test_merge_non_adjacent_rejected_422(self, client):
        p1 = _issued_parcel(client, "OG-MRG-3", square_geojson(BASE_LON, BASE_LAT, 0.001))
        p2 = _issued_parcel(client, "OG-MRG-4", square_geojson(BASE_LON + 0.005, BASE_LAT, 0.001))
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                  "child": {"parcel_uin": "OG-MRG-D",
                            "boundary_geojson": _rect_geojson(BASE_LON, BASE_LAT, 0.006, 0.001)}},
            headers=OGUN_H,
        )
        assert resp.status_code == 422
        assert "adjacency" in resp.json()["detail"].lower()

    def test_merge_area_mismatch_rejected_422(self, client):
        p1 = _issued_parcel(client, "OG-MRG-5", square_geojson(BASE_LON, BASE_LAT, 0.001))
        p2 = _issued_parcel(client, "OG-MRG-6", square_geojson(BASE_LON + 0.001, BASE_LAT, 0.001))
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                  "child": {"parcel_uin": "OG-MRG-E",
                            "boundary_geojson": _rect_geojson(BASE_LON, BASE_LAT, 0.001, 0.001)}},
            headers=OGUN_H,
        )
        assert resp.status_code == 422

    def test_merge_single_parent_rejected_422(self, client):
        p1 = _issued_parcel(client, "OG-MRG-7", square_geojson(BASE_LON, BASE_LAT, 0.001))
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [p1["parcel_id"]],
                  "child": {"parcel_uin": "OG-MRG-F",
                            "boundary_geojson": square_geojson(BASE_LON, BASE_LAT, 0.001)}},
            headers=OGUN_H,
        )
        assert resp.status_code == 422

    def test_merge_blocked_by_dispute_409(self, client):
        p1 = _issued_parcel(client, "OG-MRG-8", square_geojson(BASE_LON, BASE_LAT, 0.001))
        p2 = _issued_parcel(client, "OG-MRG-9", square_geojson(BASE_LON + 0.001, BASE_LAT, 0.001))
        _open_dispute(client, p2["parcel_id"])
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [p1["parcel_id"], p2["parcel_id"]],
                  "child": {"parcel_uin": "OG-MRG-G",
                            "boundary_geojson": _rect_geojson(BASE_LON, BASE_LAT, 0.002, 0.001)}},
            headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_merge_unknown_parent_404(self, client):
        p1 = _issued_parcel(client, "OG-MRG-10", square_geojson(BASE_LON, BASE_LAT, 0.001))
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [p1["parcel_id"], "00000000-0000-0000-0000-000000000000"],
                  "child": {"parcel_uin": "OG-MRG-H",
                            "boundary_geojson": square_geojson(BASE_LON, BASE_LAT, 0.001)}},
            headers=OGUN_H,
        )
        assert resp.status_code == 404


class TestDisputes:
    def test_open_dispute_201(self, client):
        parcel = _register(client, "OG-DSP-1", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        assert dispute["status"] == "OPENED"
        assert dispute["grounds"] == "BOUNDARY"

    def test_duplicate_open_dispute_409(self, client):
        parcel = _register(client, "OG-DSP-2", square_geojson(BASE_LON, BASE_LAT))
        _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/disputes",
            json={"complainant": "STIN-OG-1000", "grounds": "FRAUD"},
            headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_review_transition(self, client):
        parcel = _register(client, "OG-DSP-3", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/review",
            json={"actor": "tribunal-clerk"}, headers=OGUN_H,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "UNDER_REVIEW"

    def test_resolve_records_resolver_and_note(self, client):
        parcel = _register(client, "OG-DSP-4", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/resolve",
            json={"actor": "tribunal-chair", "resolution_note": "beacons re-surveyed"},
            headers=OGUN_H,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "RESOLVED"
        assert body["resolver"] == "tribunal-chair"
        assert body["resolution_note"] == "beacons re-surveyed"

    def test_dismiss(self, client):
        parcel = _register(client, "OG-DSP-5", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/dismiss",
            json={"actor": "tribunal-chair", "resolution_note": "no merit"},
            headers=OGUN_H,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "DISMISSED"

    def test_illegal_transition_resolve_after_dismiss_409(self, client):
        parcel = _register(client, "OG-DSP-6", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/dismiss",
            json={"actor": "chair"}, headers=OGUN_H,
        )
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/resolve",
            json={"actor": "chair", "resolution_note": "late"}, headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_illegal_review_after_resolve_409(self, client):
        parcel = _register(client, "OG-DSP-7", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/resolve",
            json={"actor": "chair", "resolution_note": "done"}, headers=OGUN_H,
        )
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/review",
            json={"actor": "clerk"}, headers=OGUN_H,
        )
        assert resp.status_code == 409

    def test_resolve_requires_note_422(self, client):
        parcel = _register(client, "OG-DSP-8", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/disputes/{dispute['dispute_id']}/resolve",
            json={"actor": "chair"}, headers=OGUN_H,
        )
        assert resp.status_code == 422

    def test_titling_approval_blocked_by_open_dispute_409(self, client):
        parcel = _register(client, "OG-DSP-9", square_geojson(BASE_LON, BASE_LAT))
        _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/titling/{parcel['titling_workflow_id']}/decisions",
            json={"approved": True, "actor": "surveyor"},
        )
        assert resp.status_code == 409
        assert "dispute" in resp.json()["detail"].lower()

    def test_unknown_dispute_404(self, client):
        resp = client.post(
            "/api/v1/states/ogun/cadastre/disputes/dispute-ogun-999999/review",
            json={"actor": "clerk"}, headers=OGUN_H,
        )
        assert resp.status_code == 404


class TestHistory:
    def test_history_ordering_and_instruments(self, client):
        parcel = _issued_parcel(client, "OG-HIS-1", square_geojson(BASE_LON, BASE_LAT, 0.002))
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": _halves("OG-HIS-1", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        assert resp.status_code == 201
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=OGUN_H,
        ).json()["chain_of_title"]
        instruments = [e["instrument"] for e in history]
        assert instruments[0] == "PARCEL_REGISTRATION"
        assert instruments.index("TITLING_GOVERNOR_CONSENT") < instruments.index("TITLE_ISSUED")
        assert instruments.index("PARCEL_REGISTRATION") < instruments.index("PARCEL_SUBDIVIDED")
        # chronological from_date ordering
        dates = [e["from_date"] for e in history if e["from_date"]]
        assert dates == sorted(dates)
        issued = next(e for e in history if e["instrument"] == "TITLE_ISSUED")
        assert issued["tx_reference"].startswith("OGUN/COFO/")
        assert all(e["owner_stin"] for e in history)

    def test_child_history_includes_lineage(self, client):
        parent = _issued_parcel(client, "OG-HIS-2", square_geojson(BASE_LON, BASE_LAT, 0.002))
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parent['parcel_id']}/subdivide",
            json={"children": _halves("OG-HIS-2", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        child_id = resp.json()["children"][0]["parcel_id"]
        history = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{child_id}/history", headers=OGUN_H,
        ).json()["chain_of_title"]
        instruments = [e["instrument"] for e in history]
        assert "LINEAGE_DERIVATION" in instruments
        assert "PARCEL_CREATED_FROM_SUBDIVISION" in instruments
        lineage = next(e for e in history if e["instrument"] == "LINEAGE_DERIVATION")
        assert parent["parcel_id"] in lineage["detail"]

    def test_history_unknown_parcel_404(self, client):
        resp = client.get(
            "/api/v1/states/ogun/cadastre/parcels/00000000-0000-0000-0000-000000000000/history",
            headers=OGUN_H,
        )
        assert resp.status_code == 404


class TestRisk:
    def test_fixture_determinism(self, client):
        parcel = _register(client, "OG-RSK-1", square_geojson(BASE_LON, BASE_LAT))
        url = f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/risk"
        first = client.get(url, headers=OGUN_H).json()
        second = client.get(url, headers=OGUN_H).json()
        assert first["score"] == second["score"]
        assert 0 <= first["score"] <= 100
        assert first["scorer"] == "fixture"

    def test_fixture_dispute_factor_exact_bump(self):
        scorer = FixtureTitleRiskScorer()
        # find a parcel id whose base score leaves headroom for the +30 bump
        parcel_id = next(
            f"parcel-{i}"
            for i in range(10_000)
            if scorer.score(tenant_state_id="ogun", parcel_id=f"parcel-{i}", factors=[]).score <= 60
        )
        base = scorer.score(tenant_state_id="ogun", parcel_id=parcel_id, factors=[])
        bumped = scorer.score(
            tenant_state_id="ogun", parcel_id=parcel_id,
            factors=[RiskFactor(kind="OPEN_DISPUTE", detail="open case", weight=30)],
        )
        assert bumped.score == base.score + 30

    def test_dispute_bump_via_api(self, client):
        parcel = _register(client, "OG-RSK-2", square_geojson(BASE_LON, BASE_LAT))
        url = f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/risk"
        before = client.get(url, headers=OGUN_H).json()
        _open_dispute(client, parcel["parcel_id"])
        after = client.get(url, headers=OGUN_H).json()
        kinds = [f["kind"] for f in after["factors"]]
        assert "OPEN_DISPUTE" in kinds
        assert after["score"] == min(100, before["score"] + 30)

    def test_risk_unknown_parcel_404(self, client):
        resp = client.get(
            "/api/v1/states/ogun/cadastre/parcels/00000000-0000-0000-0000-000000000000/risk",
            headers=OGUN_H,
        )
        assert resp.status_code == 404


class TestAnchoring:
    def test_anchor_and_verify_happy(self, client):
        parcel = _issued_parcel(client, "OG-ANC-1", square_geojson(BASE_LON, BASE_LAT))
        resp = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/anchor",
            headers=OGUN_H,
        )
        assert resp.status_code == 201, resp.text
        anchor = resp.json()
        assert anchor["anchor_id"].startswith("anchor-ogun-")
        assert anchor["prev_anchor_hash"] == "0" * 64  # genesis
        verify = client.get(
            f"/api/v1/states/ogun/cadastre/anchors/{anchor['anchor_id']}/verify",
            headers=OGUN_H,
        )
        assert verify.status_code == 200
        assert verify.json()["valid"] is True
        assert verify.json()["merkle_root"] == anchor["merkle_root"]

    def test_verify_detects_title_mutation(self, client):
        parcel = _issued_parcel(client, "OG-ANC-2", square_geojson(BASE_LON, BASE_LAT, 0.002))
        anchor = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/anchor",
            headers=OGUN_H,
        ).json()
        # Subdividing supersedes the parcel — the title payload changes.
        client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/subdivide",
            json={"children": _halves("OG-ANC-2", BASE_LON, BASE_LAT)}, headers=OGUN_H,
        )
        verify = client.get(
            f"/api/v1/states/ogun/cadastre/anchors/{anchor['anchor_id']}/verify",
            headers=OGUN_H,
        ).json()
        assert verify["valid"] is False
        assert "tamper" in verify["detail"].lower()

    def test_fixture_verify_tampered_payload_false(self):
        anchor = FixtureAnchor()
        payload = {"parcel_uin": "OG-1", "area_sqm": 100.0}
        record = anchor.anchor(tenant_state_id="ogun", parcel_id="p1", payload=payload)
        assert anchor.verify(tenant_state_id="ogun", anchor_id=record.anchor_id, payload=payload)
        forged = {**payload, "area_sqm": 999.0}
        assert not anchor.verify(tenant_state_id="ogun", anchor_id=record.anchor_id, payload=forged)

    def test_fixture_verify_tampered_chain_false(self):
        anchor = FixtureAnchor()
        p = {"n": 1}
        r1 = anchor.anchor(tenant_state_id="ogun", parcel_id="p1", payload=p)
        anchor.anchor(tenant_state_id="ogun", parcel_id="p2", payload={"n": 2})
        # Tamper with the stored first anchor's payload hash.
        store = anchor._anchors["ogun"]
        store[r1.anchor_id] = dataclasses.replace(
            r1, payload_hash=hashlib.sha256(b"forged").hexdigest()
        )
        assert not anchor.verify(tenant_state_id="ogun", anchor_id=r1.anchor_id, payload=p)

    def test_merkle_chain_links(self):
        anchor = FixtureAnchor()
        r1 = anchor.anchor(tenant_state_id="ogun", parcel_id="p1", payload={"n": 1})
        r2 = anchor.anchor(tenant_state_id="ogun", parcel_id="p2", payload={"n": 2})
        assert r2.prev_anchor_hash == r1.anchor_hash
        assert r1.merkle_root != r2.merkle_root

    def test_anchor_unknown_parcel_404(self, client):
        resp = client.post(
            "/api/v1/states/ogun/cadastre/parcels/00000000-0000-0000-0000-000000000000/anchor",
            headers=OGUN_H,
        )
        assert resp.status_code == 404

    def test_verify_unknown_anchor_404(self, client):
        resp = client.get(
            "/api/v1/states/ogun/cadastre/anchors/anchor-ogun-999999/verify",
            headers=OGUN_H,
        )
        assert resp.status_code == 404


class TestFailClosedAdapters:
    def test_production_boot_fails_without_risk_url(self, monkeypatch):
        monkeypatch.setenv("SOS_LANDS_PROFILE", "production")
        monkeypatch.delenv("SOS_LANDS_RISK_URL", raising=False)
        monkeypatch.setenv("SOS_LANDS_ANCHOR_URL", "http://anchor:8031")
        with pytest.raises(AdapterUnavailableError, match="SOS_LANDS_RISK_URL"):
            create_app()

    def test_production_boot_fails_without_anchor_url(self, monkeypatch):
        monkeypatch.setenv("SOS_LANDS_PROFILE", "production")
        monkeypatch.setenv("SOS_LANDS_RISK_URL", "http://ml:8021/score")
        monkeypatch.delenv("SOS_LANDS_ANCHOR_URL", raising=False)
        with pytest.raises(AdapterUnavailableError, match="SOS_LANDS_ANCHOR_URL"):
            create_app()

    def test_production_boots_with_urls_configured(self, monkeypatch):
        monkeypatch.setenv("SOS_LANDS_PROFILE", "production")
        monkeypatch.setenv("SOS_LANDS_RISK_URL", "http://ml:8021/score")
        monkeypatch.setenv("SOS_LANDS_ANCHOR_URL", "http://anchor:8031")
        app = create_app()
        assert app is not None

    def test_dev_defaults_to_fixtures(self, monkeypatch):
        monkeypatch.delenv("SOS_LANDS_PROFILE", raising=False)
        monkeypatch.delenv("SOS_LANDS_RISK_URL", raising=False)
        monkeypatch.delenv("SOS_LANDS_ANCHOR_URL", raising=False)
        assert isinstance(risk_scorer_from_env(), FixtureTitleRiskScorer)
        assert isinstance(anchor_adapter_from_env(), FixtureAnchor)

    def test_dev_honours_explicit_risk_url(self, monkeypatch):
        monkeypatch.delenv("SOS_LANDS_PROFILE", raising=False)
        monkeypatch.setenv("SOS_LANDS_RISK_URL", "http://ml:8021/score")
        assert isinstance(risk_scorer_from_env(), HttpTitleRiskScorer)


class TestTenancy:
    def test_missing_header_400_on_new_endpoints(self, client):
        parcel = _register(client, "OG-TEN-1", square_geojson(BASE_LON, BASE_LAT))
        base = f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        assert client.get(f"{base}/history").status_code == 400
        assert client.get(f"{base}/risk").status_code == 400
        assert client.get(f"{base}/disputes").status_code == 400
        assert client.post(f"{base}/anchor").status_code == 400
        assert client.post(f"{base}/subdivide", json={"children": []}).status_code == 400
        assert client.post(
            "/api/v1/states/ogun/cadastre/parcels/merge",
            json={"parent_parcel_ids": [], "child": {"parcel_uin": "x",
                  "boundary_geojson": square_geojson(BASE_LON, BASE_LAT)}},
        ).status_code == 400

    def test_header_path_mismatch_400(self, client):
        parcel = _register(client, "OG-TEN-2", square_geojson(BASE_LON, BASE_LAT))
        resp = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/history",
            headers=LAGOS_H,
        )
        assert resp.status_code == 400

    def test_cross_tenant_parcel_invisible_404(self, client):
        parcel = _register(client, "OG-TEN-3", square_geojson(BASE_LON, BASE_LAT, 0.002))
        base = f"/api/v1/states/lagos/cadastre/parcels/{parcel['parcel_id']}"
        assert client.get(f"{base}/history", headers=LAGOS_H).status_code == 404
        assert client.get(f"{base}/risk", headers=LAGOS_H).status_code == 404
        assert client.post(f"{base}/anchor", headers=LAGOS_H).status_code == 404
        assert client.get(f"{base}/disputes", headers=LAGOS_H).status_code == 404
        resp = client.post(
            f"{base}/subdivide",
            json={"children": _halves("OG-TEN-3", BASE_LON, BASE_LAT)},
            headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_cross_tenant_dispute_invisible_404(self, client):
        parcel = _register(client, "OG-TEN-4", square_geojson(BASE_LON, BASE_LAT))
        dispute = _open_dispute(client, parcel["parcel_id"])
        resp = client.post(
            f"/api/v1/states/lagos/cadastre/disputes/{dispute['dispute_id']}/review",
            json={"actor": "clerk"}, headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_cross_tenant_anchor_invisible_404(self, client):
        parcel = _register(client, "OG-TEN-5", square_geojson(BASE_LON, BASE_LAT))
        anchor = client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/anchor",
            headers=OGUN_H,
        ).json()
        resp = client.get(
            f"/api/v1/states/lagos/cadastre/anchors/{anchor['anchor_id']}/verify",
            headers=LAGOS_H,
        )
        assert resp.status_code == 404

    def test_event_log_chain_intact_after_operations(self):
        log = CadastreEventLog()
        client = TestClient(create_app(event_log=log))
        parcel = _issued_parcel(client, "OG-TEN-6", square_geojson(BASE_LON, BASE_LAT, 0.002))
        _open_dispute(client, parcel["parcel_id"])
        client.post(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}/anchor",
            headers=OGUN_H,
        )
        assert log.verify() == []
        types = [e.event_type for e in log.events("ogun")]
        assert "PARCEL_REGISTERED" in types
        assert "DISPUTE_OPENED" in types
        assert "TITLE_ANCHORED" in types
        # tenant-scoped reads see nothing for other states
        assert log.events("lagos") == []
