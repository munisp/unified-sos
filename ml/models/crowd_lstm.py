"""Small GRU/LSTM for crowd-density forecasting (CPU-sized)."""

from __future__ import annotations

import torch
from torch import nn


class CrowdLSTM(nn.Module):
    """Sequence-to-one-step density forecaster.

    Input: (batch, seq_len, 1) hourly density window; output: next-step
    density in [0,1].
    """

    def __init__(self, hidden: int = 32, num_layers: int = 1, rnn: str = "lstm"):
        super().__init__()
        cls = nn.LSTM if rnn == "lstm" else nn.GRU
        self.rnn = cls(input_size=1, hidden_size=hidden, num_layers=num_layers,
                       batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.rnn(x)
        return torch.sigmoid(self.head(out[:, -1])).squeeze(-1)


def feature_schema() -> dict:
    return {"input": "density window (seq_len, 1)", "target": "next-step density [0,1]",
            "seq_len_default": 12}
