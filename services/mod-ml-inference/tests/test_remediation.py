"""Tests for the drift remediation worker (app/remediation.py).

Torch is not required: remediation operates on drift events, not inference.
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))
_SERVICES_ROOT = SERVICE_DIR.parent
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))

from _shared.eventbus import InMemoryEventBus  # noqa: E402

from app.monitoring import DRIFT_TOPIC, DriftAlert  # noqa: E402
from app.remediation import (  # noqa: E402
    ROLLED_BACK_TOPIC,
    DriftRemediationWorker,
    build_worker,
)


def _alert(model: str = "fraud_gnn", version: str = "v3") -> DriftAlert:
    return DriftAlert(
        model_name=model,
        model_version=version,
        tenant_state_id="lagos",
        feature_psi={"amount": 0.4},
        prediction_shift=0.2,
        window_size=200,
    )


def test_first_drift_requests_retrain() -> None:
    bus = InMemoryEventBus()
    calls: list[str] = []
    worker = DriftRemediationWorker(
        bus=bus,
        on_retrain=lambda alert: calls.append(alert.model_name),
        on_rollback=lambda model: "v2",
    ).start()
    bus.publish(DRIFT_TOPIC, _alert())
    assert calls == ["fraud_gnn"]
    assert worker.retrain_requests == ["fraud_gnn"]
    assert worker.rollbacks == []
    # no rollback event published
    topics = [e["topic"] for e in bus.published]
    assert ROLLED_BACK_TOPIC not in topics


def test_second_consecutive_drift_rolls_back_and_publishes() -> None:
    bus = InMemoryEventBus()
    rolled_back: list[str] = []
    worker = DriftRemediationWorker(
        bus=bus,
        on_retrain=lambda alert: None,
        on_rollback=lambda model: rolled_back.append(model) or "v2",
    ).start()
    bus.publish(DRIFT_TOPIC, _alert())
    assert rolled_back == []
    bus.publish(DRIFT_TOPIC, _alert())
    assert rolled_back == ["fraud_gnn"]
    events = [e["payload"] for e in bus.published if e["topic"] == ROLLED_BACK_TOPIC]
    assert len(events) == 1
    payload = events[0]
    assert payload["event_type"] == "ml_model_rolled_back"
    assert payload["model_name"] == "fraud_gnn"
    assert payload["to_version"] == "v2"
    assert payload["from_version"] == "v3"


def test_drift_outside_window_requests_retrain_again() -> None:
    bus = InMemoryEventBus()
    rolled_back: list[str] = []
    worker = DriftRemediationWorker(
        bus=bus,
        on_retrain=lambda alert: None,
        on_rollback=lambda model: rolled_back.append(model) or "v2",
        window_seconds=0,  # any gap exceeds the window
    ).start()
    bus.publish(DRIFT_TOPIC, _alert())
    bus.publish(DRIFT_TOPIC, _alert())
    assert rolled_back == []
    assert len(worker.retrain_requests) == 2


def test_per_model_isolation() -> None:
    bus = InMemoryEventBus()
    rolled_back: list[str] = []
    DriftRemediationWorker(
        bus=bus,
        on_retrain=lambda alert: None,
        on_rollback=lambda model: rolled_back.append(model) or "v1",
    ).start()
    bus.publish(DRIFT_TOPIC, _alert(model="fraud_gnn"))
    bus.publish(DRIFT_TOPIC, _alert(model="credit_mlp"))
    assert rolled_back == []


def test_default_retrain_fail_soft_sets_gauge() -> None:
    from prometheus_client import CollectorRegistry, Gauge

    registry = CollectorRegistry()
    gauge = Gauge("test_retrain_requested", "t", ["model"], registry=registry)
    worker = DriftRemediationWorker(gauge=gauge)
    worker.handle_drift(_alert())
    assert gauge.labels(model="fraud_gnn")._value.get() == 1


def test_build_worker_env_gate() -> None:
    bus = InMemoryEventBus()
    assert build_worker(bus=bus, env={}) is None
    assert build_worker(bus=bus, env={"SOS_ML_REMEDIATION": "off"}) is None
    worker = build_worker(bus=bus, env={"SOS_ML_REMEDIATION": "on"},
                          on_retrain=lambda a: None)
    assert isinstance(worker, DriftRemediationWorker)
    # subscribed: publishing a drift event reaches the worker
    bus.publish(DRIFT_TOPIC, _alert())
    assert worker.retrain_requests == ["fraud_gnn"]
