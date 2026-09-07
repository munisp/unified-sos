"""CPU inference engine for mod-ml-inference.

* CPU-only: tensors are created on CPU, ``torch.set_num_threads`` is set
  from ``SOS_ML_THREADS`` (default 1), inference runs under
  ``torch.no_grad()`` with the module in ``eval()`` mode; models are loaded
  lazily and cached per ``(model_name, version)``.
* Model architectures are imported from the training stack's ``ml``
  package (``ml.models.<model_name>``) with the repo root added to
  ``sys.path`` using the same import-guard idiom as
  ``_shared.observability``. Per-model adapters know how to build each
  ``ml`` architecture from its model-card feature schema, featurize
  request instances into CPU tensors, and post-process raw outputs into
  the contract units (fraud probability, credit score 0-1000, valuation
  NGN, next-interval density).
* Card schemas in the generic ``{"features": [...]}`` list format are
  served by the local mock architectures (tests / pre-merge development).
* When no artifact is registered — or the ``ml`` package is unavailable —
  non-production profiles serve a deterministic heuristic fallback tagged
  ``model_version: fixture``. ``SOS_ML_PROFILE=production`` fails closed
  with :class:`AdapterUnavailableError` instead.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .mock_models import MOCK_ARCHITECTURES, heuristic_predict
from .registry import (
    FIXTURE_PROFILES,
    PRODUCTION_PROFILES,
    AdapterUnavailableError,
    FeatureSchema,
    ModelCard,
    ModelNotFoundError,
    ModelRegistry,
)

# --- repo-root import guard so ``ml.models.*`` resolves (same idiom as
# services/_shared/observability.py's sys.path insertion) ----------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MAX_BATCH_SIZE = 1024


class InputValidationError(ValueError):
    """Raised when a prediction request violates the feature schema."""


# --- latency tracking -----------------------------------------------------

class LatencyTracker:
    """Rolling per-model latency samples (seconds)."""

    def __init__(self, max_samples: int = 2048) -> None:
        self.max_samples = max_samples
        self._samples: Dict[str, List[float]] = {}

    def record(self, model_name: str, elapsed: float) -> None:
        samples = self._samples.setdefault(model_name, [])
        samples.append(elapsed)
        if len(samples) > self.max_samples:
            del samples[: len(samples) - self.max_samples]

    def summary(self, model_name: str) -> Dict[str, float]:
        samples = sorted(self._samples.get(model_name, []))
        if not samples:
            return {"count": 0, "mean_seconds": 0.0,
                    "p50_seconds": 0.0, "p95_seconds": 0.0}

        def pct(p: float) -> float:
            return samples[min(len(samples) - 1, int(p * len(samples)))]

        return {
            "count": len(samples),
            "mean_seconds": sum(samples) / len(samples),
            "p50_seconds": pct(0.50),
            "p95_seconds": pct(0.95),
        }


def _load_torch():
    try:
        import torch
    except ImportError as exc:  # fail-closed: torch is a runtime dep
        raise AdapterUnavailableError(
            "torch is required for inference (CPU wheel); install from the "
            "pytorch CPU index") from exc
    return torch


# --- generic list-format schema validation (mock-architecture artifacts) --

def validate_instances(schema: FeatureSchema,
                       instances: List[Dict]) -> None:
    """Validate a batch of raw instances against a list-format schema."""
    if not instances:
        raise InputValidationError("instances must be a non-empty list")
    if len(instances) > MAX_BATCH_SIZE:
        raise InputValidationError(
            f"batch size {len(instances)} exceeds max {MAX_BATCH_SIZE}")
    for index, instance in enumerate(instances):
        if not isinstance(instance, dict):
            raise InputValidationError(f"instance {index} must be an object")
        for spec in schema.features:
            if spec.name not in instance:
                raise InputValidationError(
                    f"instance {index}: missing feature {spec.name!r}")
            value = instance[spec.name]
            if spec.type == "sequence":
                if not isinstance(value, (list, tuple)) or not value:
                    raise InputValidationError(
                        f"instance {index}: feature {spec.name!r} must be a "
                        "non-empty sequence")
                if not all(isinstance(v, (int, float)) and
                           not isinstance(v, bool) for v in value):
                    raise InputValidationError(
                        f"instance {index}: feature {spec.name!r} sequence "
                        "must be numeric")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InputValidationError(
                    f"instance {index}: feature {spec.name!r} must be numeric")
            if spec.min is not None and value < spec.min:
                raise InputValidationError(
                    f"instance {index}: feature {spec.name!r} below min {spec.min}")
            if spec.max is not None and value > spec.max:
                raise InputValidationError(
                    f"instance {index}: feature {spec.name!r} above max {spec.max}")


# --- per-model adapters for the ml package architectures ------------------
#
# Each adapter provides:
#   build(schema)      -> nn.Module constructed from the card schema
#   tensorize(schema, instances, torch) -> tuple of tensor args for forward
#   post(raw_tensor, torch) -> list[float] in contract units
#
# ``build`` imports lazily so a missing ``ml`` package raises
# AdapterUnavailableError at load time (fail-closed in production).

def _require_number(instance: Dict, name: str, index: int) -> float:
    value = instance.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputValidationError(
            f"instance {index}: feature {name!r} must be numeric")
    return float(value)


def _require_category(instance: Dict, name: str, vocab: int,
                      index: int) -> int:
    value = instance.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputValidationError(
            f"instance {index}: categorical {name!r} must be an integer id")
    if not 0 <= value < vocab:
        raise InputValidationError(
            f"instance {index}: categorical {name!r} id {value} outside "
            f"vocabulary [0, {vocab})")
    return value


def _build_fraud(schema: Dict):
    try:
        from ml.models.fraud_gnn import FraudGNN
    except ImportError as exc:
        raise AdapterUnavailableError(
            "the 'ml' package is required for fraud_gnn inference") from exc
    in_dim = schema.get("in_dim") or len(schema.get("node_features", [])) or 8
    return FraudGNN(in_dim=in_dim)


def _tensorize_fraud(schema: Dict, instances: List[Dict], torch):
    feats = schema.get("node_features") or []
    if not feats:
        raise InputValidationError("fraud_gnn card schema lacks node_features")
    rows = [[_require_number(inst, f, i) for f in feats]
            for i, inst in enumerate(instances)]
    x = torch.tensor(rows, dtype=torch.float32)
    # No cross-request edges: self-loops keep GraphSAGE mean-aggregation
    # well-defined for a batch of independent nodes.
    idx = torch.arange(len(instances), dtype=torch.long)
    edge_index = torch.stack([idx, idx], dim=0)
    return (x, edge_index)


def _post_fraud(raw, torch) -> List[float]:
    return [float(v) for v in torch.sigmoid(raw).reshape(-1).tolist()]


def _embed_cat_tensor(schema: Dict, instances: List[Dict], torch,
                      fixed_order: Optional[List[str]] = None):
    cats = schema.get("categoricals") or {}
    names = fixed_order or list(cats.keys())
    rows = [[_require_category(inst, name, int(cats[name]), i)
             for name in names] for i, inst in enumerate(instances)]
    return torch.tensor(rows, dtype=torch.long)


def _num_tensor(schema: Dict, instances: List[Dict], torch):
    feats = schema.get("numeric") or []
    if not feats:
        raise InputValidationError("card schema lacks numeric features")
    rows = [[_require_number(inst, f, i) for f in feats]
            for i, inst in enumerate(instances)]
    return torch.tensor(rows, dtype=torch.float32)


def _build_credit(schema: Dict):
    try:
        from ml.models.credit_mlp import CreditMLP
    except ImportError as exc:
        raise AdapterUnavailableError(
            "the 'ml' package is required for credit_mlp inference") from exc
    return CreditMLP()


def _tensorize_credit(schema: Dict, instances: List[Dict], torch):
    # forward(cat, num) iterates dict order of CATEGORICALS: state, occupation
    cat = _embed_cat_tensor(schema, instances, torch,
                            fixed_order=["state", "occupation"])
    return (cat, _num_tensor(schema, instances, torch))


def _post_credit(raw, torch) -> List[float]:
    return [float(v) * 1000.0
            for v in torch.sigmoid(raw).reshape(-1).tolist()]


def _build_luc(schema: Dict):
    try:
        from ml.models.luc_avm import LUCAVM
    except ImportError as exc:
        raise AdapterUnavailableError(
            "the 'ml' package is required for luc_avm inference") from exc
    return LUCAVM()


def _tensorize_luc(schema: Dict, instances: List[Dict], torch):
    cat = _embed_cat_tensor(schema, instances, torch,
                            fixed_order=["state", "land_use", "h3_cell"])
    return (cat, _num_tensor(schema, instances, torch))


def _post_luc(raw, torch) -> List[float]:
    # Target was log1p(value_ngn); invert to NGN.
    return [float(v) for v in torch.expm1(raw).clamp(min=0).reshape(-1).tolist()]


def _build_crowd(schema: Dict):
    try:
        from ml.models.crowd_lstm import CrowdLSTM
    except ImportError as exc:
        raise AdapterUnavailableError(
            "the 'ml' package is required for crowd_lstm inference") from exc
    return CrowdLSTM()


def _tensorize_crowd(schema: Dict, instances: List[Dict], torch):
    key = "density" if "density" in instances[0] else "density_history"
    rows = []
    for i, inst in enumerate(instances):
        seq = inst.get(key)
        if not isinstance(seq, (list, tuple)) or not seq:
            raise InputValidationError(
                f"instance {i}: feature {key!r} must be a non-empty sequence")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in seq):
            raise InputValidationError(
                f"instance {i}: feature {key!r} sequence must be numeric")
        rows.append([float(v) for v in seq])
    seq_len = max(len(r) for r in rows)
    padded = [r + [r[-1]] * (seq_len - len(r)) for r in rows]
    # (batch, seq_len, 1)
    return (torch.tensor(padded, dtype=torch.float32).unsqueeze(-1),)


def _post_identity(raw, torch) -> List[float]:
    return [float(v) for v in raw.reshape(-1).tolist()]


class _Adapter:
    def __init__(self, build, tensorize, post):
        self.build = build
        self.tensorize = tensorize
        self.post = post


ML_ADAPTERS: Dict[str, _Adapter] = {
    "fraud_gnn": _Adapter(_build_fraud, _tensorize_fraud, _post_fraud),
    "credit_mlp": _Adapter(_build_credit, _tensorize_credit, _post_credit),
    "luc_avm": _Adapter(_build_luc, _tensorize_luc, _post_luc),
    "crowd_lstm": _Adapter(_build_crowd, _tensorize_crowd, _post_identity),
}


def _generic_schema(card_schema: Dict) -> Optional[FeatureSchema]:
    if isinstance(card_schema, dict) and "features" in card_schema:
        return FeatureSchema.model_validate(card_schema)
    return None


class InferenceEngine:
    """Lazy-loading, caching CPU inference engine."""

    def __init__(self, registry: ModelRegistry, profile: str = "fixture",
                 threads: int = 1) -> None:
        self.registry = registry
        self.profile = profile
        self._torch = _load_torch()
        self._torch.set_num_threads(max(1, int(threads)))
        # (model_name, version) -> (predict_fn, origin)
        self._cache: Dict[Tuple[str, str], Tuple[Callable, str]] = {}
        self.latency = LatencyTracker()
        self.prediction_counts: Dict[str, int] = {}

    # -- model loading ----------------------------------------------------

    def _load_generic(self, model_name: str, version: str,
                      card: ModelCard, schema: FeatureSchema):
        cls = MOCK_ARCHITECTURES.get(model_name)
        if cls is None:
            raise AdapterUnavailableError(
                f"no mock architecture for {model_name!r}")
        model = cls.from_schema(schema)
        state = self._torch.load(
            self.registry.weights_path(model_name, version),
            map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()

        def predict(instances: List[Dict]) -> List[float]:
            validate_instances(schema, instances)
            scalars = [f for f in schema.features if f.type != "sequence"]
            seqs = [f for f in schema.features if f.type == "sequence"]
            if seqs:
                seq_len = max(len(i[seqs[0].name]) for i in instances)
                rows = []
                for instance in instances:
                    seq = [list(map(float, instance[f.name])) for f in seqs]
                    padded = [s + [s[-1]] * (seq_len - len(s)) for s in seq]
                    scalar_row = [float(instance[f.name]) for f in scalars]
                    frame = list(zip(*padded))
                    rows.append([list(step) + scalar_row for step in frame])
                tensor = self._torch.tensor(rows, dtype=self._torch.float32)
            else:
                tensor = self._torch.tensor(
                    [[float(i[f.name]) for f in scalars] for i in instances],
                    dtype=self._torch.float32)
            with self._torch.no_grad():
                raw = model(tensor)
            return [float(v) for v in raw.reshape(-1).tolist()]

        return predict

    def _load_ml(self, model_name: str, version: str, card: ModelCard):
        adapter = ML_ADAPTERS.get(model_name)
        if adapter is None:
            raise AdapterUnavailableError(
                f"no inference adapter for model {model_name!r}")
        model = adapter.build(card.feature_schema)
        state = self._torch.load(
            self.registry.weights_path(model_name, version),
            map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()

        def predict(instances: List[Dict]) -> List[float]:
            args = adapter.tensorize(card.feature_schema, instances,
                                     self._torch)
            with self._torch.no_grad():
                raw = model(*args)
            return adapter.post(raw, self._torch)

        return predict

    def _load(self, model_name: str, version: str,
              card: ModelCard) -> Tuple[Callable, str]:
        key = (model_name, version)
        if key in self._cache:
            return self._cache[key]
        schema = _generic_schema(card.feature_schema)
        errors: List[str] = []
        if schema is None:
            try:
                fn = self._load_ml(model_name, version, card)
                self._cache[key] = (fn, "ml")
                return fn, "ml"
            except (AdapterUnavailableError, RuntimeError, KeyError) as exc:
                errors.append(f"ml: {exc}")
        try:
            fn = self._load_generic(model_name, version, card,
                                    schema or FeatureSchema())
            self._cache[key] = (fn, "mock")
            return fn, "mock"
        except (AdapterUnavailableError, RuntimeError, KeyError) as exc:
            errors.append(f"mock: {exc}")
        raise AdapterUnavailableError(
            f"cannot load {model_name}@{version}: " + "; ".join(errors))

    # -- prediction ---------------------------------------------------------

    def _fixture_fallback(self, model_name: str,
                          instances: List[Dict], start: float) -> Dict:
        if self.profile in PRODUCTION_PROFILES:
            raise AdapterUnavailableError(
                f"model {model_name!r} unavailable and heuristic fallback is "
                "forbidden under SOS_ML_PROFILE=production (fail-closed)")
        outputs = heuristic_predict(model_name, instances)
        elapsed = time.perf_counter() - start
        self.latency.record(model_name, elapsed)
        self.prediction_counts[model_name] = (
            self.prediction_counts.get(model_name, 0) + len(instances))
        return {
            "model_name": model_name,
            "model_version": "fixture",
            "predictions": outputs,
            "backend": "heuristic-fallback",
            "latency_seconds": elapsed,
        }

    def predict(self, model_name: str, instances: List[Dict],
                version: Optional[str] = None) -> Dict:
        """Batched predict. Returns predictions + model metadata.

        Resolution order: registry artifact (torch CPU inference) →
        deterministic heuristic fallback in non-production profiles.
        """
        start = time.perf_counter()
        try:
            card = self.registry.card(model_name, version)
        except ModelNotFoundError:
            return self._fixture_fallback(model_name, instances, start)
        try:
            predict_fn, origin = self._load(model_name, card.version, card)
        except AdapterUnavailableError:
            if self.profile in PRODUCTION_PROFILES:
                raise
            return self._fixture_fallback(model_name, instances, start)
        outputs = predict_fn(instances)
        elapsed = time.perf_counter() - start
        self.latency.record(model_name, elapsed)
        self.prediction_counts[model_name] = (
            self.prediction_counts.get(model_name, 0) + len(instances))
        result = {
            "model_name": model_name,
            "model_version": card.version,
            "predictions": outputs,
            "backend": f"torch-cpu/{origin}",
            "latency_seconds": elapsed,
        }
        if card.threshold is not None and model_name == "fraud_gnn":
            result["labels"] = [int(v >= card.threshold) for v in outputs]
        return result


def build_engine(registry: ModelRegistry,
                 env: Optional[Dict[str, str]] = None) -> InferenceEngine:
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_ML_PROFILE", "fixture")
    if profile not in FIXTURE_PROFILES + PRODUCTION_PROFILES:
        raise AdapterUnavailableError(
            f"unknown SOS_ML_PROFILE {profile!r}; expected fixture|production")
    threads = int(env.get("SOS_ML_THREADS", "1"))
    return InferenceEngine(registry, profile=profile, threads=threads)
