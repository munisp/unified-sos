"""e-C-of-O titling workflow tests: full journey to issuance, rejection,
deed verification, tenancy isolation of workflows, SLA breach flags."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lands_app.main import create_app
from lands_app.titling import LocalTitlingRunner
from tests.helpers import BASE_LAT, BASE_LON, registration_body, square_geojson

APPROVAL_STAGES = ["surveyor", "town-planning", "attorney-general", "governor"]


class MutableClock:
    """Injectable clock so tests can simulate the passage of time (SLA timers)."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: float) -> None:
        self.now += timedelta(days=days)


@pytest.fixture()
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc))


@pytest.fixture()
def client(clock) -> TestClient:
    return TestClient(create_app(titling_runner=LocalTitlingRunner(clock=clock)))


def _register(client: TestClient, state: str, uin: str, lon0: float = BASE_LON) -> dict:
    body = registration_body(uin, square_geojson(lon0, BASE_LAT))
    resp = client.post(f"/api/v1/states/{state}/cadastre/parcels", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _decide(client, state, workflow_id, actor, approved=True, note=""):
    return client.post(
        f"/api/v1/states/{state}/cadastre/titling/{workflow_id}/decisions",
        json={"approved": approved, "actor": actor, "note": note},
    )


class TestFullWorkflowToIssuance:
    def test_approve_all_stages_issues_signed_c_of_o(self, client):
        parcel = _register(client, "ogun", "OG-T-1")
        wf = parcel["titling_workflow_id"]

        status = client.get(f"/api/v1/states/ogun/cadastre/titling/{wf}").json()
        assert status["pending_stage"] == "SURVEYOR_VALIDATION"

        for actor in APPROVAL_STAGES:
            resp = _decide(client, "ogun", wf, actor)
            assert resp.status_code == 200, resp.text

        final = client.get(f"/api/v1/states/ogun/cadastre/titling/{wf}").json()
        assert final["status"] == "ISSUED"
        assert final["c_of_o_number"].startswith("OGUN/COFO/2026/")
        assert final["signed_title_jws"]

        # Parcel registry row reflects issuance (issuance activity UPDATE).
        updated = client.get(
            f"/api/v1/states/ogun/cadastre/parcels/{parcel['parcel_id']}"
        ).json()
        assert updated["title_type"] == "C_OF_O"
        assert updated["c_of_o_number"] == final["c_of_o_number"]

    def test_deed_verification_valid_after_issuance(self, client):
        parcel = _register(client, "ogun", "OG-T-2")
        wf = parcel["titling_workflow_id"]
        for actor in APPROVAL_STAGES:
            _decide(client, "ogun", wf, actor)
        c_of_o = client.get(f"/api/v1/states/ogun/cadastre/titling/{wf}").json()["c_of_o_number"]

        resp = client.post(
            "/api/v1/states/ogun/cadastre/deeds/verify",
            json={"c_of_o_number": c_of_o, "parcel_uin": "OG-T-2"},
        )
        result = resp.json()
        assert resp.status_code == 200
        assert result["valid"] is True
        assert len(result["signature_chain"]) == 2  # registry + governor consent

    def test_deed_verification_fails_for_unknown_or_foreign_tenant(self, client):
        parcel = _register(client, "ogun", "OG-T-3")
        wf = parcel["titling_workflow_id"]
        for actor in APPROVAL_STAGES:
            _decide(client, "ogun", wf, actor)
        c_of_o = client.get(f"/api/v1/states/ogun/cadastre/titling/{wf}").json()["c_of_o_number"]

        # Unknown number.
        bad = client.post(
            "/api/v1/states/ogun/cadastre/deeds/verify", json={"c_of_o_number": "FAKE/000"}
        ).json()
        assert bad["valid"] is False
        # Tenancy isolation: an Ogun title must not verify under Lagos.
        foreign = client.post(
            "/api/v1/states/lagos/cadastre/deeds/verify", json={"c_of_o_number": c_of_o}
        ).json()
        assert foreign["valid"] is False

    def test_rejection_terminates_workflow(self, client):
        parcel = _register(client, "ogun", "OG-T-4")
        wf = parcel["titling_workflow_id"]
        _decide(client, "ogun", wf, "surveyor")
        resp = _decide(client, "ogun", wf, "town-planning", approved=False, note="setback violation")
        assert resp.json()["status"] == "REJECTED"
        # Terminal workflow rejects further decisions.
        assert _decide(client, "ogun", wf, "governor").status_code == 409

    def test_workflow_invisible_across_tenants(self, client):
        parcel = _register(client, "ogun", "OG-T-5")
        wf = parcel["titling_workflow_id"]
        assert client.get(f"/api/v1/states/lagos/cadastre/titling/{wf}").status_code == 404


class TestSLAInstrumentation:
    def test_osun_45_day_total_breach_flagged(self, client, clock):
        parcel = _register(client, "osun", "OS-T-1")
        wf = parcel["titling_workflow_id"]
        clock.advance(46)  # Osun's public commitment is 45 days [LIVE]
        status = client.get(f"/api/v1/states/osun/cadastre/titling/{wf}").json()
        assert status["sla"]["total_days_allowed"] == 45
        assert status["sla"]["total_breached"] is True
        # The still-open surveyor stage (10-day clock) is also breached.
        assert any(b["stage"] == "SURVEYOR_VALIDATION" for b in status["sla"]["stage_breaches"])

    def test_benue_warns_at_60_breaches_at_90(self, client, clock):
        parcel = _register(client, "benue", "BN-T-1")
        wf = parcel["titling_workflow_id"]
        clock.advance(61)
        status = client.get(f"/api/v1/states/benue/cadastre/titling/{wf}").json()
        assert status["sla"]["warn_threshold_reached"] is True
        assert status["sla"]["total_breached"] is False
        clock.advance(30)  # 91 days total
        status = client.get(f"/api/v1/states/benue/cadastre/titling/{wf}").json()
        assert status["sla"]["total_breached"] is True

    def test_no_breach_within_sla(self, client, clock):
        parcel = _register(client, "lagos", "LA-T-1")
        wf = parcel["titling_workflow_id"]
        clock.advance(10)
        status = client.get(f"/api/v1/states/lagos/cadastre/titling/{wf}").json()
        assert status["sla"]["total_breached"] is False
        assert status["sla"]["stage_breaches"] == []
