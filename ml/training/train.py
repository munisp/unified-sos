"""Real CPU training loops for all SOS ML baseline models.

Usage:
    python -m ml.training.train --model fraud_gnn --epochs 10 --data-dir ml/data/out
    python -m ml.training.train --model all --epochs 5

Every run: Adam + cosine LR, gradient clipping, early stopping on val loss,
best-checkpoint restore, JSONL metrics log, and on success the artifact
contract is written to ``ml/artifacts/<model_name>/<version>/``:
``weights.pt`` (CPU state_dict) + ``model_card.json``.

``--ray``: if ray is importable, each model's training runs as a ray task
(guarded; default single-process). This is a seam for scale-out, not a
requirement — the baselines train in seconds on CPU.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn

from ml.data import synthetic
from ml.data.lakehouse_extract import LakehouseAdapter, random_split, time_split
from ml.models import credit_mlp, crowd_lstm, fraud_gnn, luc_avm

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACT_VERSION = "v1"  # shipped baseline

MODEL_NAMES = ["fraud_gnn", "credit_mlp", "luc_avm", "crowd_lstm"]


# ---------------------------------------------------------------- helpers --
class EarlyStopper:
    def __init__(self, patience: int = 5, min_delta: float = 1e-4):
        self.patience, self.min_delta = patience, min_delta
        self.best = float("inf")
        self.bad_epochs = 0
        self.best_state: dict | None = None

    def step(self, loss: float, model: nn.Module) -> bool:
        """Returns True when training should stop."""
        if loss < self.best - self.min_delta:
            self.best = loss
            self.bad_epochs = 0
            self.best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            return False
        self.bad_epochs += 1
        return self.bad_epochs >= self.patience


def _log_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        if "artifacts" in p.parts or "tests" in p.parts:
            continue
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(((y_true - y_pred) ** 2).sum())
    ss_tot = float(((y_true - y_true.mean()) ** 2).sum())
    return 1.0 - ss_res / max(ss_tot, 1e-12)


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank-based AUC (no sklearn dependency)."""
    order = np.argsort(p)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(p) + 1)
    pos = y == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def write_artifact(model_name: str, model: nn.Module, metrics: dict,
                   feature_schema: dict, threshold: float, dataset_hash: str,
                   out_dir: Path | None = None, version: str = ARTIFACT_VERSION) -> Path:
    """Write the inference-service artifact contract (weights + model card)."""
    dest = Path(out_dir or ARTIFACTS_DIR) / model_name / version
    dest.mkdir(parents=True, exist_ok=True)
    weights = dest / "weights.pt"
    torch.save({k: v.cpu() for k, v in model.state_dict().items()}, weights)
    card = {
        "model_name": model_name,
        "version": version,
        "metrics": metrics,
        "feature_schema": feature_schema,
        "threshold": threshold,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "dataset_hash": dataset_hash,
        "code_hash": _code_hash(),
        "framework": {"torch": torch.__version__, "device": "cpu"},
    }
    (dest / "model_card.json").write_text(json.dumps(card, indent=2))
    return dest


# ------------------------------------------------------------ data loaders --
def _adapter(data_dir: Path | None, seed: int) -> LakehouseAdapter:
    if data_dir is not None:
        # materialize fixtures into data-dir (parquet/csv) for reproducibility
        synthetic.generate_all(data_dir, seed=seed)
    return LakehouseAdapter(seed=seed)


def _cat_index(series: pd.Series, vocab: dict[str, int]) -> np.ndarray:
    return series.map(lambda v: vocab.get(v, 0)).to_numpy(dtype=np.int64)


# ------------------------------------------------------------------ fraud --
def prep_fraud(seed: int, data_dir: Path | None = None):
    df = _adapter(data_dir, seed).extract("transactions").df
    g = synthetic.build_fraud_graph(df)
    x = torch.tensor(g["x"], dtype=torch.float32)
    ei = torch.tensor(g["edge_index"], dtype=torch.long)
    y = torch.tensor(g["y"], dtype=torch.float32)
    n = x.size(0)
    perm = np.random.default_rng(seed).permutation(n)
    n_train, n_val = int(n * 0.7), int(n * 0.85)
    return dict(x=x, ei=ei, y=y,
                train_idx=torch.tensor(perm[:n_train]),
                val_idx=torch.tensor(perm[n_train:n_val]),
                test_idx=torch.tensor(perm[n_val:]),
                dataset_hash=synthetic.dataset_hash(df))


def train_fraud_gnn(epochs: int, seed: int, data_dir: Path | None, log_path: Path,
                    lr: float = 1e-2, patience: int = 6) -> tuple[nn.Module, dict, str]:
    d = prep_fraud(seed, data_dir)
    model = fraud_gnn.FraudGNN()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = nn.BCEWithLogitsLoss()
    stop = EarlyStopper(patience)
    history = []
    for epoch in range(epochs):
        model.train()
        opt.zero_grad()
        logits = model(d["x"], d["ei"])
        loss = loss_fn(logits[d["train_idx"]], d["y"][d["train_idx"]])
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            vloss = loss_fn(model(d["x"], d["ei"])[d["val_idx"]], d["y"][d["val_idx"]]).item()
        rec = {"epoch": epoch, "train_loss": round(loss.item(), 5), "val_loss": round(vloss, 5)}
        history.append(rec)
        _log_jsonl(log_path, rec)
        if stop.step(vloss, model):
            break
    if stop.best_state:
        model.load_state_dict(stop.best_state)
    model.eval()
    with torch.no_grad():
        probs = torch.sigmoid(model(d["x"], d["ei"])).numpy()
    y = d["y"].numpy()
    test_idx = d["test_idx"].numpy()
    metrics = {"val_loss": stop.best, "test_auc": _auc(y[test_idx], probs[test_idx]),
               "test_acc": float(((probs[test_idx] > 0.5) == y[test_idx]).mean()),
               "epochs_ran": len(history)}
    return model, metrics, d["dataset_hash"]


# ----------------------------------------------------------------- credit --
CREDIT_STATE_VOCAB = {s: i + 1 for i, s in enumerate(synthetic.NG_STATES)}
CREDIT_OCC_VOCAB = {o: i + 1 for i, o in enumerate(
    ["market_trader", "artisan", "transport", "agro_processor", "civil_servant"])}


def prep_credit(seed: int, data_dir: Path | None = None):
    res = _adapter(data_dir, seed).extract("credit")
    df = res.df
    train, val, test = random_split(df, seed=seed)

    def tensors(part):
        cat = torch.tensor(np.stack([
            _cat_index(part.state, CREDIT_STATE_VOCAB),
            _cat_index(part.occupation, CREDIT_OCC_VOCAB)], axis=1))
        num = part[credit_mlp.NUMERIC].copy()
        num["monthly_income_proxy_ngn"] = np.log1p(num["monthly_income_proxy_ngn"]) / 15.0
        num["avg_levy_kobo"] = np.log1p(num["avg_levy_kobo"]) / 15.0
        num["txns_last_90d"] = np.log1p(num["txns_last_90d"]) / 5.0
        num_t = torch.tensor(num.to_numpy(dtype=np.float32))
        y = torch.tensor(part.creditworthy.to_numpy(dtype=np.float32))
        return cat, num_t, y

    return dict(train=tensors(train), val=tensors(val), test=tensors(test),
                dataset_hash=res.dataset_hash)


def _batch_iter(cat, num, y, bs, rng):
    idx = rng.permutation(len(y))
    for i in range(0, len(y), bs):
        j = idx[i:i + bs]
        yield cat[j], num[j], y[j]


def train_credit_mlp(epochs: int, seed: int, data_dir: Path | None, log_path: Path,
                     lr: float = 3e-3, patience: int = 6, bs: int = 128) -> tuple[nn.Module, dict, str]:
    d = prep_credit(seed, data_dir)
    model = credit_mlp.CreditMLP()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = nn.BCEWithLogitsLoss()
    stop = EarlyStopper(patience)
    rng = np.random.default_rng(seed)
    for epoch in range(epochs):
        model.train()
        tot = 0.0
        for c, n_, yb in _batch_iter(*d["train"], bs, rng):
            opt.zero_grad()
            loss = loss_fn(model(c, n_), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item() * len(yb)
        sched.step()
        model.eval()
        with torch.no_grad():
            vloss = loss_fn(model(d["val"][0], d["val"][1]), d["val"][2]).item()
        _log_jsonl(log_path, {"epoch": epoch, "train_loss": round(tot / len(d["train"][2]), 5),
                              "val_loss": round(vloss, 5)})
        if stop.step(vloss, model):
            break
    if stop.best_state:
        model.load_state_dict(stop.best_state)
    model.eval()
    with torch.no_grad():
        probs = torch.sigmoid(model(d["test"][0], d["test"][1])).numpy()
    y = d["test"][2].numpy()
    metrics = {"val_loss": stop.best, "test_auc": _auc(y, probs),
               "test_acc": float(((probs > 0.5) == y).mean())}
    return model, metrics, d["dataset_hash"]


# -------------------------------------------------------------------- luc --
LUC_STATE_VOCAB = CREDIT_STATE_VOCAB
LUC_LU_VOCAB = {l: i + 1 for i, l in enumerate(["residential", "commercial", "industrial", "mixed"])}


def prep_luc(seed: int, data_dir: Path | None = None):
    res = _adapter(data_dir, seed).extract("properties")
    df = res.df
    train, val, test = random_split(df, seed=seed)

    def tensors(part):
        h3_idx = part.h3_cell.map(lambda h: int(h[2:]) if h[2:].isdigit() else 0)
        cat = torch.tensor(np.stack([
            _cat_index(part.state, LUC_STATE_VOCAB),
            _cat_index(part.land_use, LUC_LU_VOCAB),
            h3_idx.to_numpy(dtype=np.int64).clip(0, luc_avm.H3_VOCAB - 1)], axis=1))
        num = part[luc_avm.NUMERIC].copy()
        num["footprint_m2"] = np.log1p(num["footprint_m2"]) / 8.0
        num["floors"] = num["floors"] / 12.0
        num_t = torch.tensor(num.to_numpy(dtype=np.float32))
        y = torch.tensor(np.log1p(part.value_ngn.to_numpy(dtype=np.float32)) / 25.0)
        return cat, num_t, y

    return dict(train=tensors(train), val=tensors(val), test=tensors(test),
                dataset_hash=res.dataset_hash)


def train_luc_avm(epochs: int, seed: int, data_dir: Path | None, log_path: Path,
                  lr: float = 5e-3, patience: int = 8, bs: int = 128) -> tuple[nn.Module, dict, str]:
    d = prep_luc(seed, data_dir)
    model = luc_avm.LUCAVM()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = nn.MSELoss()
    stop = EarlyStopper(patience)
    rng = np.random.default_rng(seed)
    for epoch in range(epochs):
        model.train()
        for c, n_, yb in _batch_iter(*d["train"], bs, rng):
            opt.zero_grad()
            loss = loss_fn(model(c, n_), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            vloss = loss_fn(model(d["val"][0], d["val"][1]), d["val"][2]).item()
        _log_jsonl(log_path, {"epoch": epoch, "val_loss": round(vloss, 6)})
        if stop.step(vloss, model):
            break
    if stop.best_state:
        model.load_state_dict(stop.best_state)
    model.eval()
    with torch.no_grad():
        pred = model(d["test"][0], d["test"][1]).numpy()
    metrics = {"val_loss": stop.best, "test_r2": _r2(d["test"][2].numpy(), pred)}
    return model, metrics, d["dataset_hash"]


# ------------------------------------------------------------------ crowd --
def prep_crowd(seed: int, data_dir: Path | None = None, window: int = 12):
    res = _adapter(data_dir, seed).extract("crowd")
    df = res.df
    xs, ys = [], []
    for _, grp in df.groupby("series_id"):
        vals = grp.sort_values("step").density.to_numpy(dtype=np.float32)
        for i in range(len(vals) - window):
            xs.append(vals[i:i + window])
            ys.append(vals[i + window])
    x = torch.tensor(np.array(xs)).unsqueeze(-1)
    y = torch.tensor(np.array(ys))
    n = len(y)
    perm = np.random.default_rng(seed).permutation(n)
    n_train, n_val = int(n * 0.7), int(n * 0.85)
    sl = lambda a, i: a[torch.tensor(i)]
    return dict(train=(sl(x, perm[:n_train]), sl(y, perm[:n_train])),
                val=(sl(x, perm[n_train:n_val]), sl(y, perm[n_train:n_val])),
                test=(sl(x, perm[n_val:]), sl(y, perm[n_val:])),
                dataset_hash=res.dataset_hash, window=window)


def train_crowd_lstm(epochs: int, seed: int, data_dir: Path | None, log_path: Path,
                     lr: float = 5e-3, patience: int = 6, bs: int = 128) -> tuple[nn.Module, dict, str]:
    d = prep_crowd(seed, data_dir)
    model = crowd_lstm.CrowdLSTM()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = nn.MSELoss()
    stop = EarlyStopper(patience)
    rng = np.random.default_rng(seed)
    for epoch in range(epochs):
        model.train()
        xt, yt = d["train"]
        idx = rng.permutation(len(yt))
        for i in range(0, len(yt), bs):
            j = torch.tensor(idx[i:i + bs])
            opt.zero_grad()
            loss = loss_fn(model(xt[j]), yt[j])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            vloss = loss_fn(model(d["val"][0]), d["val"][1]).item()
        _log_jsonl(log_path, {"epoch": epoch, "val_loss": round(vloss, 6)})
        if stop.step(vloss, model):
            break
    if stop.best_state:
        model.load_state_dict(stop.best_state)
    model.eval()
    with torch.no_grad():
        pred = model(d["test"][0]).numpy()
    y = d["test"][1].numpy()
    metrics = {"val_loss": stop.best, "test_mse": float(((pred - y) ** 2).mean()),
               "test_mae": float(np.abs(pred - y).mean())}
    return model, metrics, d["dataset_hash"]


# ------------------------------------------------------------- dispatcher --
TRAINERS = {
    "fraud_gnn": (train_fraud_gnn, fraud_gnn.feature_schema, 0.5),
    "credit_mlp": (train_credit_mlp, credit_mlp.feature_schema, 0.5),
    "luc_avm": (train_luc_avm, luc_avm.feature_schema, 0.0),
    "crowd_lstm": (train_crowd_lstm, crowd_lstm.feature_schema, 0.0),
}


def train_one(model_name: str, epochs: int, seed: int, data_dir: Path | None,
              artifacts_dir: Path | None = None, log_dir: Path | None = None) -> dict:
    if torch.cuda.is_available():
        raise RuntimeError("baselines must train on CPU; CUDA device detected")
    torch.manual_seed(seed)
    np.random.seed(seed)
    fn, schema_fn, threshold = TRAINERS[model_name]
    log_path = (log_dir or (Path(artifacts_dir or ARTIFACTS_DIR) / "_logs")) / f"{model_name}.jsonl"
    if log_path.exists():
        log_path.unlink()
    model, metrics, dhash = fn(epochs, seed, data_dir, log_path)
    dest = write_artifact(model_name, model, metrics, schema_fn(), threshold, dhash,
                          out_dir=artifacts_dir)
    return {"model": model_name, "artifact": str(dest), "metrics": metrics}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Train SOS ML baseline models (CPU).")
    p.add_argument("--model", required=True, choices=MODEL_NAMES + ["all"])
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--data-dir", type=Path, default=None,
                   help="optional dir to materialize synthetic fixtures")
    p.add_argument("--artifacts-dir", type=Path, default=None)
    p.add_argument("--ray", action="store_true",
                   help="run each model as a ray task when ray is importable")
    args = p.parse_args(argv)
    names = MODEL_NAMES if args.model == "all" else [args.model]

    if args.ray:
        try:
            import ray  # type: ignore

            ray.init(ignore_reinit_error=True, include_dashboard=False)
            remote = ray.remote(train_one)
            results = ray.get([remote.remote(n, args.epochs, args.seed, args.data_dir,
                                             args.artifacts_dir) for n in names])
        except ImportError:
            print("ray not installed; falling back to single-process", file=sys.stderr)
            results = [train_one(n, args.epochs, args.seed, args.data_dir, args.artifacts_dir)
                       for n in names]
    else:
        results = [train_one(n, args.epochs, args.seed, args.data_dir, args.artifacts_dir)
                   for n in names]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
