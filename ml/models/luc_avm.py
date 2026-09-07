"""Land Use Charge automated valuation model (property valuation MLP).

Target: log1p(value_ngn) regression. Documented synthetic-data target:
R^2 >= 0.5 on the held-out test split (hedonic log-linear generator with
15% lognormal noise; see ml/data/synthetic.py::generate_properties).
"""

from __future__ import annotations

import torch
from torch import nn

CATEGORICALS = {"state": 16, "land_use": 6, "h3_cell": 10000}
NUMERIC = ["footprint_m2", "floors", "location_premium"]
H3_VOCAB = CATEGORICALS["h3_cell"]


class LUCAVM(nn.Module):
    """Embedding + MLP regressor for property valuation."""

    def __init__(self, hidden: int = 64):
        super().__init__()
        self.embeddings = nn.ModuleDict({
            "state": nn.Embedding(CATEGORICALS["state"], 8),
            "land_use": nn.Embedding(CATEGORICALS["land_use"], 4),
            "h3_cell": nn.Embedding(CATEGORICALS["h3_cell"], 8),
        })
        self.net = nn.Sequential(
            nn.Linear(8 + 4 + 8 + len(NUMERIC), hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, cat: torch.Tensor, num: torch.Tensor) -> torch.Tensor:
        embs = [self.embeddings["state"](cat[:, 0]),
                self.embeddings["land_use"](cat[:, 1]),
                self.embeddings["h3_cell"](cat[:, 2])]
        x = torch.cat(embs + [num], dim=1)
        return self.net(x).squeeze(-1)


def feature_schema() -> dict:
    return {"categoricals": CATEGORICALS, "numeric": NUMERIC,
            "target": "log1p(value_ngn)", "target_r2": ">= 0.5 (synthetic test)"}
