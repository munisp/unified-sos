"""Drift detection + performance tracking for mod-ml-inference.

* **Drift**: per-feature PSI (population stability index) of the rolling
  observation window against a baseline window (the first ``window``
  observations, or explicit ``metrics.drift_baseline`` histograms from the
  model card), plus prediction-distribution shift against the baseline
  prediction mean (``metrics.baseline_prediction_mean`` in the card, else
  the baseline window). Alerts are published to
  ``ng.sos.ml.drift_detected`` via the shared event bus.
* **Performance**: the feedback endpoint records ground-truth labels;
  rolling accuracy (threshold classification for binary labels) and AUC
  (Mann-Whitney rank statistic) are computed where labels exist.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from typing import Deque, Dict, List, Optional

from pydantic import BaseModel

DRIFT_TOPIC = "ng.sos.ml.drift_detected"
DEFAULT_PSI_THRESHOLD = 0.25
DEFAULT_WINDOW = 200
_EPS = 1e-6


class DriftAlert(BaseModel):
    """Event payload published on ``ng.sos.ml.drift_detected``."""

    event_type: str = "ml_drift_detected"
    model_name: str
    model_version: str
    tenant_state_id: str
    feature_psi: Dict[str, float] = {}
    prediction_shift: float = 0.0
    psi_threshold: float = DEFAULT_PSI_THRESHOLD
    window_size: int = 0


def psi(expected: List[float], actual: List[float], eps: float = _EPS) -> float:
    """Population stability index between two bucket distributions.

    ``expected``/``actual`` are per-bucket proportions (sums ~1). Standard
    buckets: <0.1 no shift, 0.1-0.25 moderate, >=0.25 significant drift.
    """
    if len(expected) != len(actual) or not expected:
        raise ValueError("PSI distributions must be non-empty and equal length")
    total = 0.0
    for e, a in zip(expected, actual):
        e = max(float(e), eps)
        a = max(float(a), eps)
        total += (a - e) * math.log(a / e)
    return total


def histogram(values: List[float], edges: List[float]) -> List[float]:
    """Bucket ``values`` into len(edges)+1 bins; returns proportions."""
    counts = [0] * (len(edges) + 1)
    for v in values:
        placed = False
        for i, edge in enumerate(edges):
            if v < edge:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    total = max(1, len(values))
    return [c / total for c in counts]


def _decile_edges(values: List[float], buckets: int = 10) -> List[float]:
    ordered = sorted(values)
    edges = []
    for i in range(1, buckets):
        idx = min(len(ordered) - 1, max(0, int(i * len(ordered) / buckets)))
        edges.append(ordered[idx])
    # strictly increasing edges (dedupe repeated quantiles)
    deduped: List[float] = []
    for e in edges:
        if not deduped or e > deduped[-1]:
            deduped.append(e)
    return deduped


def auc(scores: List[float], labels: List[int]) -> Optional[float]:
    """Mann-Whitney AUC; None when labels are not binary or degenerate."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


class ModelWindow:
    """Baseline + rolling observation window for one model."""

    def __init__(self, window_size: int) -> None:
        self.window_size = window_size
        self.baseline_features: Dict[str, List[float]] = {}
        self.baseline_predictions: List[float] = []
        self.recent_features: Deque[Dict[str, float]] = deque(maxlen=window_size)
        self.recent_predictions: Deque[float] = deque(maxlen=window_size)
        self.model_version: str = "fixture"
        self.tenants: set = set()

    def record(self, features: Dict[str, float], prediction: float,
               model_version: str, tenant: str) -> None:
        self.model_version = model_version
        self.tenants.add(tenant)
        if len(self.baseline_predictions) < self.window_size:
            for name, value in features.items():
                self.baseline_features.setdefault(name, []).append(value)
            self.baseline_predictions.append(prediction)
        else:
            self.recent_features.append(features)
            self.recent_predictions.append(prediction)

    def ready(self) -> bool:
        return (len(self.recent_predictions) >= self.window_size
                and len(self.baseline_predictions) >= self.window_size)


class DriftMonitor:
    """Per-model drift detection over feature + prediction distributions."""

    def __init__(self, window_size: int = DEFAULT_WINDOW,
                 psi_threshold: float = DEFAULT_PSI_THRESHOLD,
                 bus=None,
                 card_baselines: Optional[Dict[str, Dict]] = None) -> None:
        self.window_size = window_size
        self.psi_threshold = psi_threshold
        self.bus = bus
        self._lock = threading.Lock()
        self._models: Dict[str, ModelWindow] = {}
        self._card_baselines = card_baselines or {}
        self._alerted: Dict[str, bool] = {}

    def _window(self, model_name: str) -> ModelWindow:
        win = self._models.get(model_name)
        if win is None:
            win = self._models[model_name] = ModelWindow(self.window_size)
        return win

    def record_observation(self, model_name: str, features: Dict[str, float],
                           prediction: float, model_version: str,
                           tenant: str) -> None:
        with self._lock:
            self._window(model_name).record(features, prediction,
                                            model_version, tenant)
        report = self.report(model_name)
        if report["drift_detected"] and not self._alerted.get(model_name):
            self._alerted[model_name] = True
            if self.bus is not None:
                self.bus.publish(DRIFT_TOPIC, DriftAlert(
                    model_name=model_name,
                    model_version=report["model_version"],
                    tenant_state_id=tenant,
                    feature_psi=report["feature_psi"],
                    prediction_shift=report["prediction_shift"],
                    psi_threshold=self.psi_threshold,
                    window_size=report["window_size"],
                ))
        elif not report["drift_detected"]:
            self._alerted[model_name] = False

    # -- PSI computation ------------------------------------------------------

    def feature_psi(self, model_name: str) -> Dict[str, float]:
        with self._lock:
            win = self._models.get(model_name)
            if win is None or not win.recent_features:
                return {}
            baseline = dict(win.baseline_features)
            recent = list(win.recent_features)
        card_baseline = self._card_baselines.get(model_name, {})
        result: Dict[str, float] = {}
        names = set(baseline) | {n for row in recent for n in row}
        for name in sorted(names):
            expected_values = baseline.get(name, [])
            actual_values = [row[name] for row in recent if name in row]
            if not expected_values or not actual_values:
                continue
            card_hist = card_baseline.get(name)
            if card_hist and card_hist.get("edges") and card_hist.get("probs"):
                edges = list(card_hist["edges"])
                expected = list(card_hist["probs"])
            else:
                edges = _decile_edges(expected_values)
                expected = histogram(expected_values, edges)
            actual = histogram(actual_values, edges)
            result[name] = round(psi(expected, actual), 6)
        return result

    def prediction_shift(self, model_name: str,
                         card_baseline_mean: Optional[float] = None) -> float:
        """Relative |mean shift| of recent predictions vs the baseline mean."""
        with self._lock:
            win = self._models.get(model_name)
            if win is None or not win.recent_predictions:
                return 0.0
            recent = list(win.recent_predictions)
            baseline = list(win.baseline_predictions)
        base_mean = card_baseline_mean
        if base_mean is None:
            if not baseline:
                return 0.0
            base_mean = sum(baseline) / len(baseline)
        recent_mean = sum(recent) / len(recent)
        denom = max(abs(base_mean), _EPS)
        return abs(recent_mean - base_mean) / denom

    def report(self, model_name: str) -> Dict:
        with self._lock:
            win = self._models.get(model_name)
            if win is None:
                return {
                    "model_name": model_name,
                    "model_version": "fixture",
                    "window_size": 0,
                    "feature_psi": {},
                    "prediction_shift": 0.0,
                    "drift_detected": False,
                    "drifting_features": [],
                }
            snapshot = (win.model_version,
                        len(win.recent_predictions),
                        len(win.baseline_predictions))
        feature_psi = self.feature_psi(model_name)
        shift = self.prediction_shift(model_name)
        drifting = [n for n, v in feature_psi.items() if v >= self.psi_threshold]
        detected = bool(drifting) or shift >= self.psi_threshold
        return {
            "model_name": model_name,
            "model_version": snapshot[0],
            "window_size": snapshot[1],
            "baseline_size": snapshot[2],
            "psi_threshold": self.psi_threshold,
            "feature_psi": feature_psi,
            "prediction_shift": round(shift, 6),
            "drifting_features": drifting,
            "drift_detected": detected,
        }


class FeedbackStore:
    """Ground-truth labels joined to predictions → rolling accuracy/AUC."""

    def __init__(self, max_samples: int = 4096) -> None:
        self.max_samples = max_samples
        self._lock = threading.Lock()
        # model_name -> deque of (prediction, label)
        self._samples: Dict[str, Deque] = {}

    def record(self, model_name: str, prediction: float, label: float,
               prediction_id: Optional[str] = None,
               tenant_state_id: Optional[str] = None) -> None:
        with self._lock:
            samples = self._samples.get(model_name)
            if samples is None:
                samples = self._samples[model_name] = deque(maxlen=self.max_samples)
            samples.append({
                "prediction": float(prediction),
                "label": float(label),
                "prediction_id": prediction_id,
                "tenant_state_id": tenant_state_id,
            })

    def rollup(self, model_name: str,
               threshold: Optional[float] = None) -> Dict:
        """Rolling accuracy (binary labels via threshold) + AUC.

        Binary labels ({0,1}): accuracy = fraction where
        ``prediction >= threshold`` equals the label (threshold defaults to
        0.5, or the model card's threshold), plus Mann-Whitney AUC.
        Non-binary labels: accuracy = 1 - mean relative error (clipped >=0);
        AUC is not defined and reported as None.
        """
        with self._lock:
            samples = list(self._samples.get(model_name, []))
        if not samples:
            return {"model_name": model_name, "samples": 0,
                    "accuracy": None, "auc": None}
        labels = [s["label"] for s in samples]
        scores = [s["prediction"] for s in samples]
        if all(y in (0.0, 1.0) for y in labels):
            thr = 0.5 if threshold is None else threshold
            # Scores above 1 (e.g. credit 0-1000) are normalised by the
            # score range so the card threshold stays meaningful.
            scale = max(scores) if max(scores) > 1.0 else 1.0
            norm = [s / scale for s in scores]
            correct = sum(1 for s, y in zip(norm, labels)
                          if int(s >= thr) == int(y))
            return {
                "model_name": model_name,
                "samples": len(samples),
                "accuracy": correct / len(samples),
                "auc": auc(norm, [int(y) for y in labels]),
                "threshold": thr,
            }
        rel_errors = [abs(s - y) / max(abs(y), _EPS)
                      for s, y in zip(scores, labels)]
        accuracy = max(0.0, 1.0 - sum(rel_errors) / len(rel_errors))
        return {"model_name": model_name, "samples": len(samples),
                "accuracy": accuracy, "auc": None}
