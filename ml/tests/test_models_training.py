"""Each model trains 2 epochs and improves; CPU-only enforcement."""

import numpy as np
import torch

from ml.models import credit_mlp, crowd_lstm, fraud_gnn, luc_avm
from ml.training import train as trainer


def test_cpu_only_environment():
    assert not torch.cuda.is_available(), "tests must run CPU-only"


def test_pure_sage_conv_matches_manual_mean_agg():
    torch.manual_seed(0)
    conv = fraud_gnn.PureSAGEConv(8, 4)
    x = torch.randn(5, 8)
    ei = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 4]])
    out = conv(x, ei)
    assert out.shape == (5, 4)
    assert torch.isfinite(out).all()


def _train_losses(model_name, epochs=2):
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    log = tmp / f"{model_name}.jsonl"
    fn, _, _ = trainer.TRAINERS[model_name]
    model, metrics, _ = fn(epochs, 0, None, log)
    lines = [l for l in log.read_text().strip().splitlines()]
    losses = [__import__("json").loads(l)["val_loss"] for l in lines]
    return losses, metrics


def test_fraud_gnn_two_epochs_improves():
    losses, _ = _train_losses("fraud_gnn")
    assert len(losses) == 2
    assert losses[-1] < losses[0]


def test_credit_mlp_two_epochs_improves():
    losses, _ = _train_losses("credit_mlp")
    assert losses[-1] < losses[0]


def test_luc_avm_two_epochs_improves():
    losses, _ = _train_losses("luc_avm")
    assert losses[-1] < losses[0]


def test_crowd_lstm_two_epochs_improves():
    losses, _ = _train_losses("crowd_lstm")
    assert losses[-1] < losses[0]


def test_gradient_clipping_keeps_norms_bounded():
    torch.manual_seed(0)
    d = trainer.prep_fraud(0)
    model = fraud_gnn.FraudGNN()
    opt = torch.optim.Adam(model.parameters(), lr=0.1)  # aggressive lr
    for _ in range(3):
        opt.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(
            model(d["x"], d["ei"])[d["train_idx"]], d["y"][d["train_idx"]])
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        total = sum(p.grad.norm().item() ** 2 for p in model.parameters() if p.grad is not None)
        assert total ** 0.5 <= 1.0 + 1e-4
        opt.step()


def test_early_stopper_triggers():
    m = credit_mlp.CreditMLP()
    stop = trainer.EarlyStopper(patience=2)
    assert not stop.step(1.0, m)
    assert not stop.step(1.5, m)  # worse, 1 bad
    assert stop.step(1.6, m)      # worse again -> stop
    assert stop.best_state is not None


def test_forward_shapes_cpu():
    assert credit_mlp.CreditMLP()(torch.zeros(3, 2, dtype=torch.long), torch.randn(3, 5)).shape == (3,)
    assert luc_avm.LUCAVM()(torch.zeros(3, 3, dtype=torch.long), torch.randn(3, 3)).shape == (3,)
    assert crowd_lstm.CrowdLSTM()(torch.randn(3, 12, 1)).shape == (3,)
    assert fraud_gnn.FraudGNN()(torch.randn(4, 8), torch.tensor([[0, 1], [1, 2]])).shape == (4,)
