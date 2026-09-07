"""Local mock model architectures + deterministic heuristic fallback.

Two seams:

1. ``MOCK_ARCHITECTURES`` — tiny torch CPU modules used in tests when the
   ``ml`` package (model architectures built by the training workstream) is
   not merged yet. They mirror the contract: a ``from_schema`` classmethod
   sized by the model card's feature schema and a ``forward`` taking a
   float tensor batch and returning one score per row.

2. :func:`heuristic_predict` — the deterministic fixture fallback served
   when no artifact is available in a non-production profile. Predictions
   are tagged ``model_version: fixture``. Pure stdlib math (no torch), so
   the fallback path works even in minimal containers.
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, List

from .registry import FeatureSchema

try:  # torch is a hard runtime dependency, but keep the import guarded so
    # registry/monitoring modules remain importable in docs tooling.
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - torch is in requirements.txt
    torch = None
    nn = None


def _scalar_features(schema: FeatureSchema):
    return [f for f in schema.features if f.type != "sequence"]


def _sequence_features(schema: FeatureSchema):
    return [f for f in schema.features if f.type == "sequence"]


if nn is not None:

    class _LinearHead(nn.Module):
        """Shared tiny head: scalar features -> hidden -> 1 logit."""

        def __init__(self, in_dim: int) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(in_dim, 16), nn.ReLU(), nn.Linear(16, 1))

        def forward(self, x):  # (batch, in_dim) -> (batch,)
            return self.net(x).squeeze(-1)

    class FraudGNN(_LinearHead):
        """Mock graph fraud scorer (real GNN lives in ml/models/fraud_gnn.py)."""

        @classmethod
        def from_schema(cls, schema: FeatureSchema) -> "FraudGNN":
            return cls(max(1, len(_scalar_features(schema))))

        def forward(self, x):
            return torch.sigmoid(super().forward(x))

    class CreditMLP(_LinearHead):
        """Mock creditworthiness scorer -> 0..1000."""

        @classmethod
        def from_schema(cls, schema: FeatureSchema) -> "CreditMLP":
            return cls(max(1, len(_scalar_features(schema))))

        def forward(self, x):
            return torch.sigmoid(super().forward(x)) * 1000.0

    class LucAVM(_LinearHead):
        """Mock automated valuation model -> valuation in NGN."""

        @classmethod
        def from_schema(cls, schema: FeatureSchema) -> "LucAVM":
            return cls(max(1, len(_scalar_features(schema))))

        def forward(self, x):
            return torch.nn.functional.softplus(super().forward(x)) * 1e6

    class CrowdLSTM(nn.Module):
        """Mock sequence density model (real LSTM in ml/models/crowd_lstm.py)."""

        def __init__(self, in_dim: int) -> None:
            super().__init__()
            self.lstm = nn.LSTM(in_dim, 8, batch_first=True)
            self.head = nn.Linear(8, 1)

        @classmethod
        def from_schema(cls, schema: FeatureSchema) -> "CrowdLSTM":
            n_seq = len(_sequence_features(schema))
            n_scalar = len(_scalar_features(schema))
            return cls(max(1, n_seq + n_scalar))

        def forward(self, x):  # (batch, seq_len, in_dim) -> (batch,)
            out, _ = self.lstm(x)
            return torch.nn.functional.relu(self.head(out[:, -1, :])).squeeze(-1)

    MOCK_ARCHITECTURES = {
        "fraud_gnn": FraudGNN,
        "credit_mlp": CreditMLP,
        "luc_avm": LucAVM,
        "crowd_lstm": CrowdLSTM,
    }
else:  # pragma: no cover
    MOCK_ARCHITECTURES = {}


# --- deterministic heuristic fixture fallback (no torch required) -----------

def _det_uniform(*parts: str) -> float:
    """Deterministic pseudo-random uniform in [0, 1) from string parts."""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def _flat_values(instance: Dict) -> List[float]:
    values: List[float] = []
    for value in instance.values():
        if isinstance(value, (list, tuple)):
            values.extend(float(v) for v in value)
        else:
            values.append(float(value))
    return values


def heuristic_predict(model_name: str, instances: List[Dict]) -> List[float]:
    """Deterministic per-model heuristic scores (fixture profile only).

    Same inputs always yield the same outputs; tagged ``model_version:
    fixture`` by the engine so downstream consumers can filter them.
    """
    outputs: List[float] = []
    for instance in instances:
        values = _flat_values(instance)
        total = sum(values)
        noise = _det_uniform(model_name, repr(sorted(instance.items())))
        if model_name == "fraud_gnn":
            outputs.append(1.0 / (1.0 + math.exp(-(total * 0.05 + noise - 0.5))))
        elif model_name == "credit_mlp":
            score = 500.0 + total + (noise - 0.5) * 40.0
            outputs.append(min(1000.0, max(0.0, score)))
        elif model_name == "luc_avm":
            outputs.append(max(0.0, (abs(total) + 1.0) * 250_000.0 + noise))
        elif model_name == "crowd_lstm":
            seq_means = [sum(v) / len(v) for v in instance.values()
                         if isinstance(v, (list, tuple)) and v]
            base = sum(seq_means) / len(seq_means) if seq_means else total
            outputs.append(max(0.0, base + noise * 0.01))
        else:
            outputs.append(noise)
    return outputs
