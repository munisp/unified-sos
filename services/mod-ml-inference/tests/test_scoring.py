"""Tests for the /ml/v1/credit/score and /ml/v1/fraud/score contract seams.

These are the endpoints consumed by mod-mortgage (HttpCreditScorer) and
mod-gis-lands (HttpTitleRiskScorer). Fixture profile serves the
deterministic heuristic fallback tagged ``model_version: fixture``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.main import create_app  # noqa: E402
from app.registry import build_registry  # noqa: E402

TENANT = {"X-State-Tenant": "osun"}


@pytest.fixture()
def client(tmp_path) -> TestClient:
    # Empty artifacts dir → fixture profile serves the deterministic
    # heuristic fallback (model_version: "fixture"), regardless of whether
    # the repo's sample artifacts happen to be present on the test host.
    registry = build_registry({"SOS_ML_PROFILE": "fixture",
                               "SOS_ML_ARTIFACTS_DIR": str(tmp_path)})
    return TestClient(create_app(registry=registry))


def test_credit_score_shape_and_range(client: TestClient) -> None:
    resp = client.post("/ml/v1/credit/score",
                       json={"applicant_id": "applicant-001"}, headers=TENANT)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["score"], int)
    assert 300 <= body["score"] <= 850
    assert body["tenant_state_id"] == "osun"
    assert body["prediction_id"].startswith("pred-")


def test_credit_score_deterministic(client: TestClient) -> None:
    first = client.post("/ml/v1/credit/score",
                        json={"applicant_id": "applicant-777"},
                        headers=TENANT).json()
    second = client.post("/ml/v1/credit/score",
                         json={"applicant_id": "applicant-777"},
                         headers=TENANT).json()
    assert first["score"] == second["score"]
    other = client.post("/ml/v1/credit/score",
                        json={"applicant_id": "applicant-778"},
                        headers=TENANT).json()
    assert isinstance(other["score"], int)


def test_credit_score_fixture_tagged(client: TestClient) -> None:
    body = client.post("/ml/v1/credit/score",
                       json={"applicant_id": "applicant-fixture"},
                       headers=TENANT).json()
    assert body["model_version"] == "fixture"


def test_credit_score_requires_tenant(client: TestClient) -> None:
    resp = client.post("/ml/v1/credit/score", json={"applicant_id": "a"})
    assert resp.status_code == 400


def test_credit_score_explicit_features_deterministic(client: TestClient) -> None:
    features = {"income": 250000.0, "age": 41.0}
    a = client.post("/ml/v1/credit/score",
                    json={"applicant_id": "x", "features": features},
                    headers=TENANT).json()
    b = client.post("/ml/v1/credit/score",
                    json={"applicant_id": "y", "features": features},
                    headers=TENANT).json()
    assert 300 <= a["score"] <= 850
    assert a["score"] == b["score"]


def test_fraud_score_shape_and_range(client: TestClient) -> None:
    resp = client.post("/ml/v1/fraud/score",
                       json={"entity_id": "parcel-001"}, headers=TENANT)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["score"], int)
    assert 0 <= body["score"] <= 100
    assert body["tenant_state_id"] == "osun"


def test_fraud_score_deterministic_and_fixture_tagged(client: TestClient) -> None:
    first = client.post("/ml/v1/fraud/score",
                        json={"entity_id": "parcel-9"}, headers=TENANT).json()
    second = client.post("/ml/v1/fraud/score",
                         json={"entity_id": "parcel-9"}, headers=TENANT).json()
    assert first["score"] == second["score"]
    assert first["model_version"] == "fixture"


def test_fraud_score_requires_tenant(client: TestClient) -> None:
    resp = client.post("/ml/v1/fraud/score", json={"entity_id": "p"})
    assert resp.status_code == 400
