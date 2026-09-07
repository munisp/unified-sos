"""Artifact contract, registry promote/rollback, lakehouse adapter."""

import json

import torch

from ml.data.lakehouse_extract import LakehouseAdapter, random_split, time_split, validate_schema
from ml.models import crowd_lstm
from ml.registry import ModelRegistry, RegistryError
from ml.training import train as trainer
from ml.training.continuous import ChampionChallenger, ContinuousTrainer


# ------------------------------------------------------------- artifact ----
def test_artifact_contract_roundtrip(tmp_path):
    result = trainer.train_one("crowd_lstm", epochs=2, seed=0, data_dir=None,
                               artifacts_dir=tmp_path)
    dest = tmp_path / "crowd_lstm" / "v1"
    card = json.loads((dest / "model_card.json").read_text())
    # required contract fields
    for f in ("model_name", "version", "metrics", "feature_schema", "threshold",
              "trained_at", "dataset_hash"):
        assert f in card, f"missing card field {f}"
    assert card["model_name"] == "crowd_lstm"
    # weights load on CPU into a fresh model
    state = torch.load(dest / "weights.pt", map_location="cpu", weights_only=True)
    model = crowd_lstm.CrowdLSTM()
    model.load_state_dict(state)
    assert not next(model.parameters()).is_cuda


def test_artifact_weights_small(tmp_path):
    trainer.train_one("credit_mlp", epochs=1, seed=0, data_dir=None, artifacts_dir=tmp_path)
    size = (tmp_path / "credit_mlp" / "v1" / "weights.pt").stat().st_size
    assert size < 5 * 1024 * 1024


# ------------------------------------------------------------- registry ----
def _register_dummy(reg, name, value, metric=0.9):
    w = tmp_weights(value)
    card = {"metrics": {"test_auc": metric}, "feature_schema": {}, "threshold": 0.5,
            "dataset_hash": "abc", "trained_at": "now"}
    return reg.register(name, w, card), w


def tmp_weights(value):
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / f"w{value}.pt"
    torch.save({"bias": torch.tensor([float(value)])}, p)
    return p


def test_registry_register_and_promote(tmp_path):
    reg = ModelRegistry(tmp_path)
    v1, _ = _register_dummy(reg, "m", 1, 0.90)
    v2, _ = _register_dummy(reg, "m", 2, 0.95)
    assert reg.versions("m") == ["v1", "v2"]
    assert reg.current_version("m") == "v2"
    assert reg.load_card("m")["metrics"]["test_auc"] == 0.95


def test_registry_rollback(tmp_path):
    reg = ModelRegistry(tmp_path)
    _register_dummy(reg, "m", 1, 0.90)
    _register_dummy(reg, "m", 2, 0.95)
    prev = reg.rollback("m")
    assert prev == "v1" and reg.current_version("m") == "v1"
    import pytest
    with pytest.raises(RegistryError):
        reg.rollback("m")  # no earlier version


def test_registry_no_promote_flag(tmp_path):
    reg = ModelRegistry(tmp_path)
    _register_dummy(reg, "m", 1, 0.9)
    _register_dummy(reg, "m", 2, 0.8)
    # register without promotion
    w = tmp_weights(3)
    reg.register("m", w, {"metrics": {"test_auc": 0.7}}, promote=False)
    assert reg.current_version("m") == "v2"


def test_mlflow_seam_fail_closed(tmp_path, monkeypatch):
    import importlib.util
    import pytest
    if importlib.util.find_spec("mlflow") is not None:
        pytest.skip("mlflow installed; fail-closed path not applicable")
    monkeypatch.setenv("SOS_MLFLOW_TRACKING_URI", "http://mlflow:5000")
    with pytest.raises(RegistryError):
        ModelRegistry(tmp_path)  # mlflow not installed -> must raise


# ------------------------------------------------- champion / challenger ----
def test_promotion_margin_higher_is_better():
    g = ChampionChallenger(margin=0.01)
    assert g.should_promote("test_auc", None, 0.5)          # first model always
    assert not g.should_promote("test_auc", 0.90, 0.905)    # below margin
    assert g.should_promote("test_auc", 0.90, 0.92)         # beats margin


def test_promotion_margin_lower_is_better():
    g = ChampionChallenger(margin=0.01)
    assert not g.should_promote("test_mse", 0.10, 0.095)
    assert g.should_promote("test_mse", 0.10, 0.08)


def test_continuous_trainer_no_new_extracts(tmp_path):
    ct = ContinuousTrainer(tmp_path / "empty", registry=ModelRegistry(tmp_path / "reg"),
                           epochs=1)
    assert ct.check_once("credit_mlp") is None


def test_continuous_trainer_promotes_first_model(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "credit.csv").write_text("placeholder")  # marker of new extract
    ct = ContinuousTrainer(watch, registry=ModelRegistry(tmp_path / "reg"),
                           epochs=2, artifacts_dir=tmp_path / "art")
    out = ct.check_once("credit_mlp")
    assert out["promoted"] is True and out["version"] == "v1"
    # second cycle: same files -> no retrain
    assert ct.check_once("credit_mlp") is None


# -------------------------------------------------------------- adapter ----
def test_adapter_fixture_default():
    a = LakehouseAdapter(uri=None, seed=42)
    assert a.mode == "fixture"
    res = a.extract("credit")
    assert res.source == "fixture:synthetic"
    assert len(res.dataset_hash) == 16


def test_adapter_live_seam(tmp_path):
    from ml.data import synthetic
    df = synthetic.generate_credit(n=100, seed=1)
    df.to_csv(tmp_path / "credit.csv", index=False)
    a = LakehouseAdapter(uri=str(tmp_path))
    assert a.mode == "live"
    res = a.extract("credit")
    assert len(res.df) == 100


def test_adapter_fail_closed_bad_uri():
    import pytest
    with pytest.raises(FileNotFoundError):
        LakehouseAdapter(uri="/nonexistent/lakehouse")


def test_schema_validation():
    import pandas as pd
    import pytest
    with pytest.raises(ValueError):
        validate_schema("credit", pd.DataFrame({"state": []}))
    with pytest.raises(ValueError):
        validate_schema("credit", pd.DataFrame())  # empty


def test_time_split_no_leakage():
    import pandas as pd
    df = pd.DataFrame({"ts": pd.date_range("2024-01-01", periods=100), "v": range(100)})
    tr, va, te = time_split(df, "ts")
    assert tr.ts.max() < va.ts.min() < te.ts.min()
    assert len(tr) == 70 and len(va) == 15 and len(te) == 15
