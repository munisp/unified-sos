"""Tabular credit-worthiness MLP with categorical embeddings (CPU-sized)."""

from __future__ import annotations

import torch
from torch import nn

CATEGORICALS = {"state": 16, "occupation": 8}
EMB_DIM = 8
NUMERIC = ["monthly_income_proxy_ngn", "seasonality_index", "igr_percentile",
           "txns_last_90d", "avg_levy_kobo"]


class CreditMLP(nn.Module):
    """Embedding + MLP binary classifier for informal-sector credit scoring."""

    def __init__(self, hidden: int = 48, emb_dim: int = EMB_DIM):
        super().__init__()
        self.embeddings = nn.ModuleDict(
            {k: nn.Embedding(v, emb_dim) for k, v in CATEGORICALS.items()})
        in_dim = emb_dim * len(CATEGORICALS) + len(NUMERIC)
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(0.1),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, cat: torch.Tensor, num: torch.Tensor) -> torch.Tensor:
        embs = [self.embeddings[k](cat[:, i]) for i, k in enumerate(CATEGORICALS)]
        x = torch.cat(embs + [num], dim=1)
        return self.net(x).squeeze(-1)


def feature_schema() -> dict:
    return {"categoricals": CATEGORICALS, "numeric": NUMERIC, "emb_dim": EMB_DIM,
            "target": "creditworthy (binary)"}
