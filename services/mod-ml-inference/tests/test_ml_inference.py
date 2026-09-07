"""Tests for mod-ml-inference (registry, inference, drift, A/B, feedback).

Run from the service directory::

    cd services/mod-ml-inference && python -m pytest tests -v

Model architectures are the local mock modules (app/mock_models.py) because
the training stack's ``ml`` package is developed in parallel; the contract
tested here (card → schema → state_dict → batched CPU predict) is identical.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.ab import ABRouter, verify_event_chain  # noqa: E402
from app.inference import (  # noqa: E402
    InferenceEngine,
    InputValidationError,
    validate_instances,
)
from app.main import create_app  # noqa: E402
from app.mock_models import MOCK_ARCHITECTURES, heuristic_predict  # noqa: E402
from app.monitoring import (  # noqa: E402
    DRIFT_TOPIC,
    DriftMonitor,
    FeedbackStore,
    psi,
)
from app.registry import (  # noqa: E402
    AdapterUnavailableError,
    ModelNotFoundError,
    ModelRegistry,
    build_registry,
)

TENANT = {"X-State-Tenant": "lagos"}

SCHEMAS = {
    "fraud_gnn": {"features": [
        {"name": "in_degree", "type": "float"},
        {"name": "out_degree", "type": "float"},
        {"name": "tx_amount", "type": "float"},
    ]},
    "credit_mlp": {"features": [
        {"name": "income", "type": "float"},
        {"name": "age", "type": "int", "min": 0, "max": 120},
        {"name": "debt_ratio", "type": "float", "min": 0, "max": 1},
    ]},
    "luc_avm": {"features": [
        {"name": "area_m2", "type": "float", "min": 0},
        {"name": "rooms", "type": "int", "min": 0},
        {"name": "latitude", "type": "float", "min": -90, "max": 90},
        {"name": "longitude", "type": "float", "min": -180, "max": 180},
    ]},
    "crowd_lstm": {"features": [
        {"name": "density_history", "type": "sequence"},
        {"name": "hour", "type": "float", "min": 0, "max": 23},
    ]},
}

INSTANCES = {
    "fraud_gnn": [{"in_degree": 3.0, "out_degree": 1.0, "tx_amount": 500.0}],
    "credit_mlp": [{"income": 250_000.0, "age": 34, "debt_ratio": 0.2}],
    "luc_avm": [{"area_m2": 120.0, "rooms": 3, "latitude": 6.5, "longitude": 3.4}],
    "crowd_lstm": [{"density_history": [0.1, 0.2, 0.3, 0.4], "hour": 8.0}],
}


def _make_card(model_name: str, version: str) -> dict:
    return {
        "model_name": model_name,
        "version": version,
        "metrics": {"auc": 0.9, "baseline_prediction_mean": 0.5},
        "feature_schema": SCHEMAS[model_name],
        "threshold": 0.5 if model_name in ("fraud_gnn", "credit_mlp") else None,
        "trained_at": "2025-01-01T00:00:00Z",
        "dataset_hash": "sha256:deadbeef",
    }


def _write_artifact(root: Path, model_name: str, version: str,
                    champion: bool = False, challenger: bool = False) -> None:
    from app.registry import FeatureSchema

    version_dir = root / model_name / version
    version_dir.mkdir(parents=True, exist_ok=True)
    schema = FeatureSchema.model_validate(SCHEMAS[model_name])
    model = MOCK_ARCHITECTURES[model_name].from_schema(schema)
    torch.save(model.state_dict(), version_dir / "weights.pt")
    (version_dir / "model_card.json").write_text(json.dumps(
        _make_card(model_name, version)))
    if champion:
        (root / model_name / "champion").write_text(version)
    if challenger:
        (root / model_name / "challenger").write_text(version)


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    for name in SCHEMAS:
        _write_artifact(root, name, "v1", champion=True)
    _write_artifact(root, "fraud_gnn", "v2", challenger=True)
    return root


@pytest.fixture()
def registry(artifacts_dir: Path) -> ModelRegistry:
    return ModelRegistry(artifacts_dir)


@pytest.fixture()
def engine(registry: ModelRegistry) -> InferenceEngine:
    return InferenceEngine(registry, profile="fixture", threads=1)


@pytest.fixture()
def client(registry: ModelRegistry, engine: InferenceEngine) -> TestClient:
    monitor = DriftMonitor(window_size=8)
    app = create_app(registry=registry, engine=engine, monitor=monitor,
                     router=ABRouter(challenger_pct=50.0))
    return TestClient(app)


# --- registry ------------------------------------------------------------

def test_registry_lists_models_and_versions(registry: ModelRegistry):
    assert registry.models() == ["credit_mlp", "crowd_lstm", "fraud_gnn", "luc_avm"]
    assert registry.versions("fraud_gnn") == ["v1", "v2"]


def test_registry_champion_and_challenger_pointers(registry: ModelRegistry):
    assert registry.champion_version("fraud_gnn") == "v1"
    assert registry.challenger_version("fraud_gnn") == "v2"
    assert registry.challenger_version("credit_mlp") is None


def test_registry_card_contract(registry: ModelRegistry):
    card = registry.card("luc_avm")
    assert card.model_name == "luc_avm"
    assert card.version == "v1"
    assert card.dataset_hash == "sha256:deadbeef"
    assert {f["name"] for f in card.feature_schema["features"]} == {
        "area_m2", "rooms", "latitude", "longitude"}


def test_registry_unknown_model_raises(registry: ModelRegistry):
    with pytest.raises(ModelNotFoundError):
        registry.card("nonexistent")


def test_registry_incomplete_artifact_skipped(artifacts_dir: Path):
    broken = artifacts_dir / "fraud_gnn" / "v3"
    broken.mkdir()
    (broken / "model_card.json").write_text(json.dumps(
        _make_card("fraud_gnn", "v3")))  # no weights.pt
    reg = ModelRegistry(artifacts_dir)
    assert "v3" not in reg.versions("fraud_gnn")


def test_build_registry_production_fails_closed(tmp_path: Path):
    env = {"SOS_ML_PROFILE": "production",
           "SOS_ML_ARTIFACTS_DIR": str(tmp_path / "empty")}
    with pytest.raises(AdapterUnavailableError):
        build_registry(env)


def test_build_registry_production_mlflow_seam_fails_closed(artifacts_dir):
    # mlflow package is not installed → tracking URI alone must not pass.
    env = {"SOS_ML_PROFILE": "production",
           "SOS_ML_ARTIFACTS_DIR": str(artifacts_dir),
           "SOS_MLFLOW_TRACKING_URI": "http://mlflow:5000"}
    with pytest.raises(AdapterUnavailableError):
        build_registry(env)


def test_build_registry_production_ok_with_artifacts(artifacts_dir: Path):
    env = {"SOS_ML_PROFILE": "production",
           "SOS_ML_ARTIFACTS_DIR": str(artifacts_dir)}
    reg = build_registry(env)
    assert "fraud_gnn" in reg.models()


# --- inference contract (mock architectures) --------------------------------

def test_predict_fraud_gnn_probability(engine: InferenceEngine):
    result = engine.predict("fraud_gnn", INSTANCES["fraud_gnn"])
    assert result["backend"] == "torch-cpu/mock"
    assert result["model_version"] == "v1"
    assert 0.0 <= result["predictions"][0] <= 1.0
    assert result["labels"][0] in (0, 1)


def test_predict_credit_mlp_score_range(engine: InferenceEngine):
    result = engine.predict("credit_mlp", INSTANCES["credit_mlp"])
    assert 0.0 <= result["predictions"][0] <= 1000.0


def test_predict_luc_avm_positive_ngn(engine: InferenceEngine):
    result = engine.predict("luc_avm", INSTANCES["luc_avm"])
    assert result["predictions"][0] > 0.0


def test_predict_crowd_lstm_sequence(engine: InferenceEngine):
    result = engine.predict("crowd_lstm", INSTANCES["crowd_lstm"])
    assert result["predictions"][0] >= 0.0


def test_predict_batched(engine: InferenceEngine):
    batch = INSTANCES["fraud_gnn"] * 4
    result = engine.predict("fraud_gnn", batch)
    assert len(result["predictions"]) == 4


def test_predict_deterministic_same_weights(engine: InferenceEngine):
    first = engine.predict("credit_mlp", INSTANCES["credit_mlp"])
    second = engine.predict("credit_mlp", INSTANCES["credit_mlp"])
    assert first["predictions"] == second["predictions"]


def test_fixture_fallback_deterministic(engine: InferenceEngine):
    instances = [{"in_degree": 1.0, "out_degree": 2.0, "tx_amount": 10.0}]
    first = engine.predict("fraud_gnn_v_next", instances)
    second = engine.predict("fraud_gnn_v_next", instances)
    assert first["model_version"] == "fixture"
    assert first["backend"] == "heuristic-fallback"
    assert first["predictions"] == second["predictions"]
    # heuristic helpers are pure functions of inputs
    assert heuristic_predict("credit_mlp", [{"x": 1}]) == \
        heuristic_predict("credit_mlp", [{"x": 1}])


def test_fixture_fallback_forbidden_in_production(registry: ModelRegistry):
    prod = InferenceEngine(registry, profile="production", threads=1)
    with pytest.raises(AdapterUnavailableError):
        prod.predict("unregistered_model", [{"x": 1}])


def test_input_validation_missing_feature(engine: InferenceEngine):
    with pytest.raises(InputValidationError):
        engine.predict("credit_mlp", [{"income": 1.0}])


def test_input_validation_bounds():
    from app.registry import FeatureSchema

    schema = FeatureSchema.model_validate(SCHEMAS["credit_mlp"])
    with pytest.raises(InputValidationError):
        validate_instances(schema, [{"income": 1.0, "age": 200,
                                     "debt_ratio": 0.5}])
    with pytest.raises(InputValidationError):
        validate_instances(schema, [])


def test_latency_tracked(engine: InferenceEngine):
    engine.predict("fraud_gnn", INSTANCES["fraud_gnn"])
    summary = engine.latency.summary("fraud_gnn")
    assert summary["count"] == 1
    assert summary["mean_seconds"] >= 0.0


# --- real ml-package architectures (training stack contract) ---------------

ML_INSTANCES = {
    "fraud_gnn": [{
        "log_total_amount": 5.0, "txn_count_log": 2.0,
        "unique_counterparties_log": 1.0, "mean_hour_norm": 0.5,
        "under_threshold_ratio": 0.1, "is_payer": 1.0, "is_agent": 0.0,
        "is_beneficiary": 0.0,
    }],
    "credit_mlp": [{
        "state": 3, "occupation": 2, "monthly_income_proxy_ngn": 150000.0,
        "seasonality_index": 0.4, "igr_percentile": 0.6, "txns_last_90d": 12.0,
        "avg_levy_kobo": 50000.0,
    }],
    "luc_avm": [{
        "state": 3, "land_use": 1, "h3_cell": 1234, "footprint_m2": 120.0,
        "floors": 2.0, "location_premium": 1.3,
    }],
    "crowd_lstm": [{"density": [0.2, 0.3, 0.4, 0.5, 0.4, 0.3]}],
}


@pytest.fixture()
def ml_artifacts_dir(tmp_path: Path) -> Path:
    """Artifacts written against the real ``ml`` package architectures."""
    pytest.importorskip("ml.models.fraud_gnn")
    from ml.models.credit_mlp import CreditMLP
    from ml.models.crowd_lstm import CrowdLSTM
    from ml.models.fraud_gnn import FraudGNN, feature_schema as fraud_schema
    from ml.models.luc_avm import LUCAVM

    root = tmp_path / "ml_artifacts"
    builders = {
        "fraud_gnn": (FraudGNN(in_dim=8), fraud_schema()),
        "credit_mlp": (CreditMLP(), {"categoricals": {"state": 16,
                                                      "occupation": 8},
                                     "numeric": ["monthly_income_proxy_ngn",
                                                 "seasonality_index",
                                                 "igr_percentile",
                                                 "txns_last_90d",
                                                 "avg_levy_kobo"]}),
        "luc_avm": (LUCAVM(), {"categoricals": {"state": 16, "land_use": 6,
                                                "h3_cell": 10000},
                               "numeric": ["footprint_m2", "floors",
                                           "location_premium"]}),
        "crowd_lstm": (CrowdLSTM(), {"input": "density window (seq_len, 1)",
                                     "target": "next-step density [0,1]"}),
    }
    for name, (module, schema) in builders.items():
        version_dir = root / name / "v1"
        version_dir.mkdir(parents=True)
        torch.save(module.state_dict(), version_dir / "weights.pt")
        card = _make_card(name, "v1")
        card["feature_schema"] = schema
        (version_dir / "model_card.json").write_text(json.dumps(card))
    return root


@pytest.fixture()
def ml_engine(ml_artifacts_dir: Path) -> InferenceEngine:
    return InferenceEngine(ModelRegistry(ml_artifacts_dir),
                           profile="production", threads=1)


def test_ml_fraud_gnn_probability_per_node(ml_engine: InferenceEngine):
    result = ml_engine.predict("fraud_gnn", ML_INSTANCES["fraud_gnn"] * 3)
    assert result["backend"] == "torch-cpu/ml"
    assert len(result["predictions"]) == 3
    assert all(0.0 <= p <= 1.0 for p in result["predictions"])
    assert result["labels"][0] in (0, 1)


def test_ml_credit_mlp_score_0_1000(ml_engine: InferenceEngine):
    result = ml_engine.predict("credit_mlp", ML_INSTANCES["credit_mlp"])
    assert 0.0 <= result["predictions"][0] <= 1000.0


def test_ml_luc_avm_valuation_ngn(ml_engine: InferenceEngine):
    result = ml_engine.predict("luc_avm", ML_INSTANCES["luc_avm"])
    assert result["predictions"][0] >= 0.0  # expm1(log1p NGN), clamped


def test_ml_crowd_lstm_next_density(ml_engine: InferenceEngine):
    result = ml_engine.predict("crowd_lstm", ML_INSTANCES["crowd_lstm"])
    assert 0.0 <= result["predictions"][0] <= 1.0


def test_ml_credit_categorical_vocab_validation(ml_engine: InferenceEngine):
    bad = dict(ML_INSTANCES["credit_mlp"][0], state=99)
    with pytest.raises(InputValidationError):
        ml_engine.predict("credit_mlp", [bad])


# --- API surface -----------------------------------------------------------

def test_healthz(client: TestClient):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_models_endpoint_lists_cards(client: TestClient):
    response = client.get("/ml/v1/models", headers=TENANT)
    assert response.status_code == 200
    names = {m["model_name"] for m in response.json()["models"]}
    assert names == set(SCHEMAS)
    fraud = next(m for m in response.json()["models"]
                 if m["model_name"] == "fraud_gnn")
    assert fraud["champion_version"] == "v1"
    assert fraud["challenger_version"] == "v2"


def test_predict_endpoint_tenant_scoped(client: TestClient):
    missing = client.post("/ml/v1/predict/fraud_gnn",
                          json={"instances": INSTANCES["fraud_gnn"]})
    assert missing.status_code == 400
    ok = client.post("/ml/v1/predict/fraud_gnn",
                     json={"instances": INSTANCES["fraud_gnn"]},
                     headers=TENANT)
    assert ok.status_code == 200
    body = ok.json()
    assert body["tenant_state_id"] == "lagos"
    assert body["prediction_id"].startswith("pred-")


def test_predict_endpoint_unknown_model_404(client: TestClient):
    # fixture profile: unregistered model → heuristic fallback (200)
    response = client.post("/ml/v1/predict/brand_new",
                           json={"instances": [{"x": 1}]}, headers=TENANT)
    assert response.status_code == 200
    assert response.json()["model_version"] == "fixture"


def test_predict_endpoint_validation_422(client: TestClient):
    response = client.post("/ml/v1/predict/credit_mlp",
                           json={"instances": [{"income": 1.0}]},
                           headers=TENANT)
    assert response.status_code == 422


def test_metrics_endpoint_exposes_ml_series(client: TestClient):
    client.post("/ml/v1/predict/fraud_gnn",
                json={"instances": INSTANCES["fraud_gnn"]}, headers=TENANT)
    text = client.get("/metrics").text
    assert 'ml_predictions_total{model="fraud_gnn"}' in text
    assert 'service_info{service="mod-ml-inference"' in text


def test_predictions_audit_logged_inputs_hash_only(client: TestClient):
    client.post("/ml/v1/predict/luc_avm",
                json={"instances": INSTANCES["luc_avm"]}, headers=TENANT)
    audit = client.app.state.audit.records
    assert len(audit) == 1
    record = audit[0]
    assert record["tenant_state_id"] == "lagos"
    assert len(record["inputs_hash"]) == 64
    # no raw feature values leak into the audit chain
    assert "area_m2" not in json.dumps(record)
    assert verify_event_chain(audit) == []


# --- drift (PSI math + injected drift) -------------------------------------

def test_psi_identical_distributions_zero():
    dist = [0.1] * 10
    assert psi(dist, dist) == pytest.approx(0.0, abs=1e-9)


def test_psi_shifted_distribution_positive():
    expected = [0.5, 0.5]
    actual = [0.1, 0.9]
    assert psi(expected, actual) > 0.25


def test_drift_monitor_no_drift():
    monitor = DriftMonitor(window_size=8)
    for i in range(16):
        value = (i % 8) / 8.0  # same distribution in baseline and window
        monitor.record_observation("fraud_gnn", {"in_degree": value},
                                   0.1 + (i % 8) * 0.01, "v1", "lagos")
    report = monitor.report("fraud_gnn")
    assert report["drift_detected"] is False
    assert all(v < 0.25 for v in report["feature_psi"].values())


def test_drift_monitor_injected_drift_alerts():
    events = []

    class _Bus:
        def publish(self, topic, payload):
            events.append((topic, payload))

    monitor = DriftMonitor(window_size=8, bus=_Bus())
    for i in range(8):  # baseline around 0..1
        monitor.record_observation("fraud_gnn", {"in_degree": i / 8.0},
                                   0.2, "v1", "lagos")
    for _ in range(8):  # injected shift far outside baseline range
        monitor.record_observation("fraud_gnn", {"in_degree": 100.0},
                                   0.2, "v1", "lagos")
    report = monitor.report("fraud_gnn")
    assert report["drift_detected"] is True
    assert report["feature_psi"]["in_degree"] >= 0.25
    assert "in_degree" in report["drifting_features"]
    assert events and events[0][0] == DRIFT_TOPIC
    assert events[0][1].model_name == "fraud_gnn"


def test_drift_prediction_shift_detected():
    monitor = DriftMonitor(window_size=8, psi_threshold=0.25)
    for _ in range(8):
        monitor.record_observation("credit_mlp", {"income": 1.0}, 500.0,
                                   "v1", "lagos")
    for _ in range(8):
        monitor.record_observation("credit_mlp", {"income": 1.0}, 900.0,
                                   "v1", "lagos")
    report = monitor.report("credit_mlp")
    assert report["prediction_shift"] >= 0.25
    assert report["drift_detected"] is True


def test_drift_endpoint(client: TestClient):
    response = client.get("/ml/v1/drift/fraud_gnn", headers=TENANT)
    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "fraud_gnn"
    assert body["drift_detected"] is False


# --- A/B routing -------------------------------------------------------------

def test_ab_routing_deterministic_by_key():
    router = ABRouter(challenger_pct=50.0)
    first = router.assign("fraud_gnn", "entity-123", "v1", "v2")
    for _ in range(10):
        assert router.assign("fraud_gnn", "entity-123", "v1", "v2") == first


def test_ab_routing_zero_pct_always_champion():
    router = ABRouter(challenger_pct=0.0)
    for key in ("a", "b", "c", "d"):
        assert router.assign("fraud_gnn", key, "v1", "v2") == ("champion", "v1")


def test_ab_routing_full_pct_always_challenger():
    router = ABRouter(challenger_pct=100.0)
    assert router.assign("fraud_gnn", "anything", "v1", "v2") == ("challenger", "v2")


def test_ab_assignments_hash_chained():
    router = ABRouter(challenger_pct=100.0)
    for key in ("k1", "k2", "k3"):
        variant, version = router.assign("fraud_gnn", key, "v1", "v2")
        router.log_assignment("fraud_gnn", key, variant, version, "lagos")
        router.log_outcome("fraud_gnn", variant, version, "pred-x", 0.01)
    assert verify_event_chain(router.chain) == []
    # key itself never stored — only its hash
    assert "k1" not in json.dumps(router.chain)


def test_ab_comparison_endpoint(client: TestClient):
    for i in range(4):
        client.post("/ml/v1/predict/fraud_gnn",
                    json={"instances": INSTANCES["fraud_gnn"],
                          "key": f"entity-{i}"},
                    headers=TENANT)
    body = client.get("/ml/v1/ab/fraud_gnn", headers=TENANT).json()
    assert body["challenger_pct"] == 50.0
    total = sum(v["assignments"] for v in body["variants"].values())
    assert total == 4
    assert body["chain_valid"] is True


# --- feedback rollup --------------------------------------------------------

def test_feedback_accuracy_perfect_labels():
    store = FeedbackStore()
    for score, label in ((0.9, 1), (0.8, 1), (0.1, 0), (0.2, 0)):
        store.record("fraud_gnn", score, label)
    roll = store.rollup("fraud_gnn", threshold=0.5)
    assert roll["samples"] == 4
    assert roll["accuracy"] == 1.0
    assert roll["auc"] == 1.0


def test_feedback_auc_ranking():
    store = FeedbackStore()
    store.record("fraud_gnn", 0.9, 0)  # wrong ranking
    store.record("fraud_gnn", 0.1, 1)
    roll = store.rollup("fraud_gnn")
    assert roll["accuracy"] == 0.0
    assert roll["auc"] == 0.0


def test_feedback_non_binary_labels_relative_accuracy():
    store = FeedbackStore()
    store.record("luc_avm", 105.0, 100.0)
    store.record("luc_avm", 95.0, 100.0)
    roll = store.rollup("luc_avm")
    assert roll["auc"] is None
    assert roll["accuracy"] == pytest.approx(0.95)


def test_feedback_endpoint_accepts_and_rolls_up(client: TestClient):
    response = client.post("/ml/v1/feedback/fraud_gnn",
                           json={"prediction": 0.9, "label": 1},
                           headers=TENANT)
    assert response.status_code == 202
    assert response.json()["rollup"]["samples"] == 1
    roll = client.get("/ml/v1/feedback/fraud_gnn", headers=TENANT).json()
    assert roll["samples"] == 1
