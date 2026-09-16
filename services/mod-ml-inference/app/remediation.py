"""Closed-loop drift remediation for mod-ml-inference.

:meth:`DriftMonitor.record_observation` publishes ``ng.sos.ml.drift_detected``
but, without this worker, nothing subscribed and ``ml/registry.py``
``rollback()`` had no caller. The :class:`DriftRemediationWorker` closes the
loop (WP: ML lifecycle automation):

  * first significant drift for a model  → retrain requested (injected
    ``on_retrain`` callback; default fail-soft: log + set the Prometheus
    gauge ``ml_retrain_requested{model}`` to 1);
  * a second consecutive significant drift for the *same* model within
    ``window_seconds`` (retrain did not fix it) → ``on_rollback`` (default:
    ``ml.registry.ModelRegistry.rollback``) and publish
    ``ng.sos.ml.model_rolled_back``.

The worker is in-process and subscribes via the shared event-bus idiom
(``services/_shared/eventbus``, ``EventBus.subscribe``). Wiring is opt-in:
``SOS_ML_REMEDIATION=on`` in ``main.py`` (default off so the deterministic
fixture profile is unchanged).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from pydantic import BaseModel

from .monitoring import DRIFT_TOPIC, DriftAlert

logger = logging.getLogger("sos.ml.remediation")

ROLLED_BACK_TOPIC = "ng.sos.ml.model_rolled_back"
DEFAULT_WINDOW_SECONDS = 3600.0


class ModelRolledBackEvent(BaseModel):
    """Payload published on ``ng.sos.ml.model_rolled_back``."""

    event_type: str = "ml_model_rolled_back"
    model_name: str
    from_version: str
    to_version: str
    reason: str = "consecutive_significant_drift"
    timestamp: float


_GAUGE = None


def _retrain_gauge():
    """Import-guarded Prometheus gauge singleton; None in minimal images."""
    global _GAUGE
    if _GAUGE is not None:
        return _GAUGE
    try:
        from prometheus_client import Gauge
    except ImportError:  # pragma: no cover - minimal container images
        return None
    _GAUGE = Gauge(
        "ml_retrain_requested",
        "Drift remediation requested a retrain for this model (1 = pending).",
        ["model"],
    )
    return _GAUGE


class DriftRemediationWorker:
    """Subscribes to drift alerts; requests retrain, escalates to rollback."""

    def __init__(
        self,
        bus=None,
        on_retrain: Optional[Callable[[DriftAlert], None]] = None,
        on_rollback: Optional[Callable[[str], str]] = None,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        gauge=None,
    ) -> None:
        self.bus = bus
        self.window_seconds = float(window_seconds)
        self._on_retrain = on_retrain or self._default_on_retrain
        self._on_rollback = on_rollback or self._default_on_rollback
        self._gauge = gauge if gauge is not None else _retrain_gauge()
        self._lock = threading.Lock()
        # model_name -> timestamp of the last significant drift alert.
        self._last_drift: Dict[str, float] = {}
        self.retrain_requests: list[str] = []
        self.rollbacks: list[str] = []

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> "DriftRemediationWorker":
        """Subscribe to the drift topic (no-op without a bus)."""
        if self.bus is not None and hasattr(self.bus, "subscribe"):
            self.bus.subscribe(DRIFT_TOPIC, self.handle_drift)
        return self

    # -- event handling ---------------------------------------------------------
    def handle_drift(self, alert) -> None:
        """Handle one ``ng.sos.ml.drift_detected`` payload (DriftAlert/dict)."""
        if isinstance(alert, dict):
            alert = DriftAlert(**alert)
        model = alert.model_name
        now = time.time()
        with self._lock:
            previous = self._last_drift.get(model)
            self._last_drift[model] = now
            second_consecutive = (
                previous is not None and (now - previous) <= self.window_seconds
            )
        if second_consecutive:
            self._rollback(model, alert)
        else:
            self._request_retrain(alert)

    # -- actions ----------------------------------------------------------------
    def _request_retrain(self, alert: DriftAlert) -> None:
        self.retrain_requests.append(alert.model_name)
        try:
            self._on_retrain(alert)
        except Exception:  # fail-soft: remediation must never break inference
            logger.exception("retrain callback failed for %s", alert.model_name)

    def _rollback(self, model: str, alert: DriftAlert) -> None:
        try:
            to_version = self._on_rollback(model)
        except Exception:
            logger.exception("rollback failed for %s", model)
            return
        self.rollbacks.append(model)
        if self.bus is not None:
            self.bus.publish(ROLLED_BACK_TOPIC, ModelRolledBackEvent(
                model_name=model,
                from_version=alert.model_version,
                to_version=to_version,
                timestamp=time.time(),
            ))

    # -- defaults (fail-soft) ---------------------------------------------------
    def _default_on_retrain(self, alert: DriftAlert) -> None:
        logger.warning(
            "significant drift for %s@%s (tenant %s) — retrain requested",
            alert.model_name, alert.model_version, alert.tenant_state_id,
        )
        if self._gauge is not None:
            self._gauge.labels(model=alert.model_name).set(1)

    def _default_on_rollback(self, model: str) -> str:
        """Rollback via the training-side registry (ml/registry.py)."""
        try:
            from ml.registry import ModelRegistry as _TrainingRegistry
        except ImportError:
            import sys
            from pathlib import Path
            root = Path(__file__).resolve().parents[4]
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from ml.registry import ModelRegistry as _TrainingRegistry
        return _TrainingRegistry().rollback(model)


def build_worker(bus=None, env: Optional[Dict[str, str]] = None,
                 **kwargs) -> Optional[DriftRemediationWorker]:
    """Create and start the worker when ``SOS_ML_REMEDIATION=on``.

    Default off: the fixture/test profile never mutates model state.
    """
    import os

    env = env if env is not None else dict(os.environ)
    if env.get("SOS_ML_REMEDIATION", "off").lower() not in ("on", "1", "true"):
        return None
    worker = DriftRemediationWorker(bus=bus, **kwargs)
    return worker.start()
