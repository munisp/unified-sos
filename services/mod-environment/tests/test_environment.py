"""mod-environment: telemetry compliance, permits, deforestation alerts,
carbon registry, EIA workflow, tenant isolation, OpenAPI generation."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

LAGOS_PM25_VIOLATION = {
    "tenant_state_id": "lagos",
    "facility_id": "FAC-LG-001",
    "sensor_id": "SEN-AQ-11",
    "medium": "AIR",
    "parameter": "PM2_5",
    "value": 65.0,
    "unit": "ug/m3",
}

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[11.0, 7.0], [11.1, 7.0], [11.1, 7.1], [11.0, 7.1], [11.0, 7.0]]],
}


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def post_telemetry(client, **over):
    payload = dict(LAGOS_PM25_VIOLATION)
    payload.update(over)
    return client.post("/environment/v1/telemetry", json=payload)


def make_project(client, state="taraba"):
    r = client.post(
        "/environment/v1/carbon-projects",
        json={
            "tenant_state_id": state,
            "name": "Gashaka REDD+",
            "boundary": POLYGON,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def make_credit(client, state="taraba", serial="NG-TAR-2026-0001", price=1_000_000_00):
    project = make_project(client, state)
    r = client.post(
        "/environment/v1/carbon-credits",
        json={
            "tenant_state_id": state,
            "project_id": project["project_id"],
            "serial": serial,
            "vintage": 2026,
            "quantity_tco2e": 500.0,
            "price_kobo": price,
            "owner_id": "STATE-GREEN-FUND",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestTelemetryCompliance:
    def test_compliant_reading(self, client):
        r = post_telemetry(client, value=10.0)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "COMPLIANT"
        assert body["incident"] is None

    def test_warning_reading_no_incident(self, client):
        r = post_telemetry(client, value=40.0)  # between 35 and 50
        assert r.json()["status"] == "WARNING"
        assert r.json()["incident"] is None

    def test_violation_creates_incident_with_state_fine_multiplier(self, client):
        r = post_telemetry(client, value=65.0)  # >= 50 violation threshold
        body = r.json()
        assert body["status"] == "VIOLATION"
        incident = body["incident"]
        assert incident is not None
        # base N5,000,000 x lagos multiplier 2.0 = N10,000,000 (kobo)
        assert incident["fine_estimate_kobo"] == 1_000_000_000
        assert incident["fine_multiplier"] == 2.0
        assert incident["ledger_account_code"] == "3001"

    def test_ogun_multiplier_differs_from_lagos(self, client):
        r = post_telemetry(client, tenant_state_id="ogun", value=65.0)
        assert r.json()["incident"]["fine_estimate_kobo"] == 750_000_000

    def test_taraba_forestry_adjacent_default_limit(self, client):
        r = post_telemetry(
            client,
            tenant_state_id="taraba",
            medium="WATER",
            parameter="BOD",
            unit="mg/L",
            value=28.0,  # taraba violation threshold 25
        )
        assert r.json()["status"] == "VIOLATION"
        assert r.json()["incident"]["fine_multiplier"] == 1.0

    def test_unmapped_parameter_defaults_compliant(self, client):
        r = post_telemetry(client, parameter="COD", unit="mg/L", value=999.0)
        # COD limit exists for lagos (threshold 120) -> violation; use noise instead
        r = post_telemetry(client, medium="NOISE", unit="dB", value=999.0)
        assert r.json()["status"] == "COMPLIANT"

    def test_facility_compliance_summary(self, client):
        post_telemetry(client, value=10.0)
        post_telemetry(client, value=65.0)
        r = client.get(
            "/environment/v1/facilities/FAC-LG-001/compliance",
            params={"state_id": "lagos"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["readings_evaluated"] == 2
        assert body["violations"] == 1
        assert body["compliance_rate"] == 0.5
        assert body["total_fines_kobo"] == 1_000_000_000

    def test_facility_compliance_tenant_isolation(self, client):
        post_telemetry(client, value=65.0)
        r = client.get(
            "/environment/v1/facilities/FAC-LG-001/compliance",
            params={"state_id": "ogun"},
        )
        body = r.json()
        assert body["readings_evaluated"] == 0
        assert body["violations"] == 0

    def test_unknown_tenant_rejected(self, client):
        r = post_telemetry(client, tenant_state_id="kano")
        assert r.status_code == 422


class TestPermits:
    PERMIT = {
        "tenant_state_id": "lagos",
        "permit_type": "EFFLUENT_DISCHARGE",
        "holder_id": "ACME-IND",
        "facility_id": "FAC-LG-001",
        "fee_kobo": 250_000_00,
    }

    def test_permit_lifecycle_draft_to_active(self, client):
        r = client.post("/environment/v1/permits", json=self.PERMIT)
        assert r.status_code == 201, r.text
        permit = r.json()
        assert permit["status"] == "DRAFT"
        r = client.post(
            f"/environment/v1/permits/{permit['permit_id']}/activate",
            params={"state_id": "lagos"},
        )
        assert r.json()["status"] == "ACTIVE"

    def test_activate_twice_rejected(self, client):
        permit = client.post("/environment/v1/permits", json=self.PERMIT).json()
        url = f"/environment/v1/permits/{permit['permit_id']}/activate"
        client.post(url, params={"state_id": "lagos"})
        assert client.post(url, params={"state_id": "lagos"}).status_code == 409

    def test_permit_cross_tenant_blocked(self, client):
        permit = client.post("/environment/v1/permits", json=self.PERMIT).json()
        r = client.post(
            f"/environment/v1/permits/{permit['permit_id']}/activate",
            params={"state_id": "ogun"},
        )
        assert r.status_code == 404

    def test_timber_permit_taraba(self, client):
        payload = dict(self.PERMIT, tenant_state_id="taraba",
                       permit_type="TIMBER_LOGGING", holder_id="LIC-TAR-4410")
        r = client.post("/environment/v1/permits", json=payload)
        assert r.status_code == 201


class TestDeforestationAlerts:
    ALERT = {
        "tenant_state_id": "taraba",
        "polygon": POLYGON,
        "h3_cells": ["8841f1a4dffffff"],
        "ndvi_delta": 0.42,
        "area_hectares": 3.7,
        "source": "SENTINEL2",
    }

    def test_alert_computes_4h_sla(self, client):
        detected = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)
        payload = dict(self.ALERT, detected_at=detected.isoformat())
        r = client.post("/environment/v1/deforestation-alerts", json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        deadline = datetime.fromisoformat(body["sla_deadline"])
        assert deadline == detected + timedelta(hours=4)

    def test_dispatch_creates_sec10_ticket(self, client):
        alert = client.post(
            "/environment/v1/deforestation-alerts", json=self.ALERT
        ).json()
        r = client.post(
            f"/environment/v1/deforestation-alerts/{alert['alert_id']}/dispatch",
            params={"state_id": "taraba"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "DISPATCHED"
        assert body["enforcement_ticket_ref"].startswith("SEC10-")

    def test_dispatch_twice_rejected(self, client):
        alert = client.post(
            "/environment/v1/deforestation-alerts", json=self.ALERT
        ).json()
        url = f"/environment/v1/deforestation-alerts/{alert['alert_id']}/dispatch"
        client.post(url, params={"state_id": "taraba"})
        assert client.post(url, params={"state_id": "taraba"}).status_code == 409

    def test_dispatch_cross_tenant_blocked(self, client):
        alert = client.post(
            "/environment/v1/deforestation-alerts", json=self.ALERT
        ).json()
        r = client.post(
            f"/environment/v1/deforestation-alerts/{alert['alert_id']}/dispatch",
            params={"state_id": "lagos"},
        )
        assert r.status_code == 404


class TestCarbonRegistry:
    def test_issue_transfer_retire_flow_with_brokerage(self, client):
        credit = make_credit(client)
        cid = credit["credit_id"]
        r = client.post(
            f"/environment/v1/carbon-credits/{cid}/issue",
            params={"state_id": "taraba"},
        )
        assert r.json()["status"] == "ISSUED"
        r = client.post(
            f"/environment/v1/carbon-credits/{cid}/transfer",
            json={"tenant_state_id": "taraba", "new_owner_id": "BUYER-IND-7"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["credit"]["status"] == "TRANSFERRED"
        assert body["credit"]["owner_id"] == "BUYER-IND-7"
        # [DERIVED] 3% brokerage on N1,000,000 price = N30,000
        assert body["brokerage_fee_kobo"] == 30_000_00
        legs = body["settlement_lines"]
        assert sum(l["amount_kobo"] for l in legs) == 1_000_000_00
        r = client.post(
            f"/environment/v1/carbon-credits/{cid}/retire",
            params={"state_id": "taraba"},
        )
        assert r.json()["status"] == "RETIRED"

    def test_retirement_is_terminal(self, client):
        credit = make_credit(client)
        cid = credit["credit_id"]
        client.post(f"/environment/v1/carbon-credits/{cid}/issue",
                    params={"state_id": "taraba"})
        client.post(f"/environment/v1/carbon-credits/{cid}/retire",
                    params={"state_id": "taraba"})
        r = client.post(f"/environment/v1/carbon-credits/{cid}/retire",
                        params={"state_id": "taraba"})
        assert r.status_code == 409
        r = client.post(
            f"/environment/v1/carbon-credits/{cid}/transfer",
            json={"tenant_state_id": "taraba", "new_owner_id": "X"},
        )
        assert r.status_code == 409

    def test_duplicate_serial_rejected(self, client):
        make_credit(client, serial="NG-TAR-2026-0001")
        project = make_project(client)
        r = client.post(
            "/environment/v1/carbon-credits",
            json={
                "tenant_state_id": "taraba",
                "project_id": project["project_id"],
                "serial": "NG-TAR-2026-0001",
                "vintage": 2026,
                "quantity_tco2e": 10.0,
                "owner_id": "FUND",
            },
        )
        assert r.status_code == 409

    def test_transfer_requires_issued_status(self, client):
        credit = make_credit(client)
        r = client.post(
            f"/environment/v1/carbon-credits/{credit['credit_id']}/transfer",
            json={"tenant_state_id": "taraba", "new_owner_id": "X"},
        )
        assert r.status_code == 409

    def test_credit_cross_tenant_blocked(self, client):
        credit = make_credit(client)
        r = client.post(
            f"/environment/v1/carbon-credits/{credit['credit_id']}/issue",
            params={"state_id": "lagos"},
        )
        assert r.status_code == 404

    def test_state_brokerage_override(self, client):
        from app.main import create_app as factory
        from app.service import EnvironmentService

        svc = EnvironmentService(brokerage_bps={"taraba": 500})
        with TestClient(factory(svc)) as c:
            credit = make_credit(c, price=1_000_000_00)
            c.post(
                f"/environment/v1/carbon-credits/{credit['credit_id']}/issue",
                params={"state_id": "taraba"},
            )
            r = c.post(
                f"/environment/v1/carbon-credits/{credit['credit_id']}/transfer",
                json={"tenant_state_id": "taraba", "new_owner_id": "X"},
            )
            assert r.json()["brokerage_fee_kobo"] == 50_000_00  # 5%


class TestEIA:
    EIA = {
        "tenant_state_id": "ogun",
        "project_name": "Ijebu Cement Plant Expansion",
        "facility_id": "FAC-OG-204",
        "category": "CATEGORY_1",
        "documents": ["eia-report.pdf", "baseline-study.pdf"],
    }

    def test_happy_path_to_approval(self, client):
        r = client.post("/environment/v1/eias", json=self.EIA)
        assert r.status_code == 201, r.text
        app = r.json()
        assert app["status"] == "SUBMITTED"
        assert app["temporal_workflow_ref"].startswith("WF-EIA-")
        url = f"/environment/v1/eias/{app['application_id']}/advance"
        r = client.post(url, json={"tenant_state_id": "ogun", "target": "SCREENING"})
        assert r.json()["status"] == "SCREENING"
        r = client.post(
            url, json={"tenant_state_id": "ogun", "target": "PUBLIC_COMMENT"}
        )
        assert r.json()["status"] == "PUBLIC_COMMENT"
        r = client.post(
            url,
            json={
                "tenant_state_id": "ogun",
                "target": "APPROVED",
                "decision_reason": "mitigation plan accepted",
            },
        )
        assert r.json()["status"] == "APPROVED"
        assert r.json()["decision_reason"] == "mitigation plan accepted"

    def test_reject_path(self, client):
        app = client.post("/environment/v1/eias", json=self.EIA).json()
        url = f"/environment/v1/eias/{app['application_id']}/advance"
        r = client.post(
            url,
            json={
                "tenant_state_id": "ogun",
                "target": "REJECTED",
                "decision_reason": "incomplete baseline study",
            },
        )
        assert r.json()["status"] == "REJECTED"

    def test_illegal_transition_rejected(self, client):
        app = client.post("/environment/v1/eias", json=self.EIA).json()
        r = client.post(
            f"/environment/v1/eias/{app['application_id']}/advance",
            json={
                "tenant_state_id": "ogun",
                "target": "APPROVED",
                "decision_reason": "skip",
            },
        )
        assert r.status_code == 409

    def test_decision_requires_reason(self, client):
        app = client.post("/environment/v1/eias", json=self.EIA).json()
        url = f"/environment/v1/eias/{app['application_id']}/advance"
        client.post(url, json={"tenant_state_id": "ogun", "target": "SCREENING"})
        client.post(
            url, json={"tenant_state_id": "ogun", "target": "PUBLIC_COMMENT"}
        )
        r = client.post(
            url, json={"tenant_state_id": "ogun", "target": "APPROVED"}
        )
        assert r.status_code == 409


class TestPlatform:
    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["module"] == "mod-environment"

    def test_openapi_generation(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        assert "/environment/v1/telemetry" in paths
        assert "/environment/v1/carbon-credits/{credit_id}/retire" in paths
        assert "/environment/v1/eias/{application_id}/advance" in paths
        assert "/healthz" in paths
