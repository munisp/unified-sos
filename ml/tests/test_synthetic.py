"""Generator determinism, label sanity, IO fallback."""

import numpy as np
import pandas as pd

from ml.data import synthetic


def test_transactions_deterministic():
    a = synthetic.generate_transactions(n=500, seed=3)
    b = synthetic.generate_transactions(n=500, seed=3)
    pd.testing.assert_frame_equal(a, b)


def test_transactions_seed_changes_output():
    a = synthetic.generate_transactions(n=500, seed=3)
    b = synthetic.generate_transactions(n=500, seed=4)
    assert not a.equals(b)


def test_fraud_rate_within_range():
    df = synthetic.generate_transactions(n=4000, seed=42)
    rate = df.fraud.mean()
    assert 0.02 < rate < 0.12, f"fraud rate {rate} out of plausible range"


def test_fraud_patterns_present():
    df = synthetic.generate_transactions(n=2000, seed=1)
    pats = set(df[df.fraud == 1].pattern.unique())
    assert {"ghost_payroll", "split_payment", "agent_collusion", "round_trip"} <= pats


def test_split_payment_under_threshold():
    df = synthetic.generate_transactions(n=2000, seed=1)
    sp = df[df.pattern == "split_payment"]
    assert (sp.amount_kobo < synthetic.LEVY_THRESHOLD_KOBO).all()


def test_credit_deterministic_and_label_rate():
    a = synthetic.generate_credit(n=1000, seed=7)
    b = synthetic.generate_credit(n=1000, seed=7)
    pd.testing.assert_frame_equal(a, b)
    rate = a.creditworthy.mean()
    assert 0.2 < rate < 0.9


def test_properties_deterministic_positive_values():
    a = synthetic.generate_properties(n=500, seed=11)
    pd.testing.assert_frame_equal(a, synthetic.generate_properties(n=500, seed=11))
    assert (a.value_ngn > 0).all()
    assert (a.floors >= 1).all()


def test_crowd_density_bounds():
    df = synthetic.generate_crowd(seq_len=24, n_series=20, seed=13)
    assert df.density.between(0, 1).all()


def test_write_frame_csv_fallback(tmp_path, monkeypatch):
    # simulate missing pyarrow: write_frame must fall back to csv
    import builtins
    real_import = builtins.__import__

    def no_pyarrow(name, *args, **kwargs):
        if name == "pyarrow":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    df = synthetic.generate_credit(n=50, seed=2)
    out = synthetic.write_frame(df, tmp_path / "credit.parquet")
    assert out.suffix == ".csv" and out.exists()
    back = synthetic.read_frame(tmp_path / "credit.parquet")
    assert len(back) == 50


def test_fraud_graph_shapes_and_labels():
    df = synthetic.generate_transactions(n=800, seed=5)
    g = synthetic.build_fraud_graph(df)
    n = g["x"].shape[0]
    assert g["x"].shape[1] == 8
    assert g["edge_index"].shape[0] == 2
    assert g["y"].shape == (n,)
    assert g["y"].max() == 1 and g["y"].mean() < 0.5  # minority fraud class


def test_dataset_hash_stable():
    df = synthetic.generate_credit(n=100, seed=9)
    assert synthetic.dataset_hash(df) == synthetic.dataset_hash(df.copy())
