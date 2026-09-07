"""Fraud-ring detection GNN over the payer-agent-beneficiary transaction graph.

GraphSAGE-style encoder + binary node classifier. Uses torch_geometric's
SAGEConv when available; otherwise a pure-torch mean-aggregator fallback
(``PureSAGEConv``) with identical math: h_v = ReLU(W_self h_v + W_neigh
mean_{u in N(v)} h_u). CPU-friendly sizes only.
"""

from __future__ import annotations

import torch
from torch import nn

try:  # optional accel: real PyG layers when installed
    from torch_geometric.nn import SAGEConv as _PyGSAGEConv  # type: ignore

    HAS_PYG = True
except ImportError:  # pure-torch fallback
    HAS_PYG = False


class PureSAGEConv(nn.Module):
    """GraphSAGE mean-aggregator convolution in pure torch (CPU-safe)."""

    def __init__(self, in_dim: int, out_dim: int):
        super().__init__()
        self.lin_self = nn.Linear(in_dim, out_dim)
        self.lin_neigh = nn.Linear(in_dim, out_dim)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        src, dst = edge_index[0], edge_index[1]
        # mean aggregate neighbor messages into dst
        agg = torch.zeros_like(x)
        agg.index_add_(0, dst, x[src])
        deg = torch.zeros(x.size(0), device=x.device, dtype=x.dtype)
        deg.index_add_(0, dst, torch.ones_like(dst, dtype=x.dtype))
        agg = agg / deg.clamp(min=1.0).unsqueeze(1)
        return torch.relu(self.lin_self(x) + self.lin_neigh(agg))


def _conv_cls():
    return _PyGSAGEConv if HAS_PYG else PureSAGEConv


class FraudGNN(nn.Module):
    """2-layer GraphSAGE encoder + node-level fraud classifier."""

    def __init__(self, in_dim: int = 8, hidden: int = 32):
        super().__init__()
        conv = _conv_cls()
        self.conv1 = conv(in_dim, hidden)
        self.conv2 = conv(hidden, hidden)
        self.head = nn.Linear(hidden, 1)
        self.feature_dim = in_dim
        self.hidden = hidden

    def encode(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        h = self.conv1(x, edge_index)
        if not HAS_PYG:
            pass  # PureSAGEConv already applies ReLU
        else:
            h = torch.relu(h)
        return self.conv2(h, edge_index)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """Return per-node fraud logits."""
        return self.head(self.encode(x, edge_index)).squeeze(-1)


def feature_schema() -> dict:
    return {
        "node_features": ["log_total_amount", "txn_count_log", "unique_counterparties_log",
                          "mean_hour_norm", "under_threshold_ratio",
                          "is_payer", "is_agent", "is_beneficiary"],
        "edge_schema": ["payer->agent", "agent->beneficiary"],
        "in_dim": 8,
    }
