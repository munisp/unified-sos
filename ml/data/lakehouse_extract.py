"""Lakehouse -> training extraction adapters (fail-closed).

Reads training frames from the platform lakehouse layout
(``services/lakehouse/lakehouse/medallion.py``: bronze/silver/gold layers,
silver canonical columns ``event_id, tenant_state_id, revenue_head, channel,
amount_kobo, payer_ref, event_ts, ingest_ts``).

Adapter contract mirrors the repo's fail-closed idiom: unless
``SOS_ML_LAKEHOUSE_URI`` is set to a live lakehouse root, the default
adapter generates deterministic fixtures from ``ml.data.synthetic`` — so
the training stack never silently reads unvalidated production data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ml.data import synthetic

LAKEHOUSE_URI_ENV = "SOS_ML_LAKEHOUSE_URI"

#: Expected columns per dataset (schema validation contract).
EXPECTED_SCHEMAS: dict[str, set[str]] = {
    "transactions": {"payer_ref", "agent_ref", "beneficiary_ref", "amount_kobo", "ts", "state", "fraud", "pattern"},
    "credit": {"state", "occupation", "monthly_income_proxy_ngn", "seasonality_index",
               "igr_percentile", "txns_last_90d", "avg_levy_kobo", "creditworthy"},
    "properties": {"state", "h3_cell", "land_use", "footprint_m2", "floors", "location_premium", "value_ngn"},
    "crowd": {"series_id", "step", "density"},
}


@dataclass
class ExtractResult:
    name: str
    df: pd.DataFrame
    source: str  # "fixture" or the live URI
    dataset_hash: str


class LakehouseAdapter:
    """Fail-closed extraction adapter.

    Default: fixture adapter backed by ``ml.data.synthetic``.
    Live seam: set ``SOS_ML_LAKEHOUSE_URI`` to a directory containing
    gold-layer parquet/csv files named ``<dataset>.parquet`` (or .csv).
    Unknown URIs raise — the adapter never guesses.
    """

    def __init__(self, uri: str | None = None, seed: int = 42):
        self.uri = uri if uri is not None else os.environ.get(LAKEHOUSE_URI_ENV)
        self.seed = seed
        if self.uri is not None and not Path(self.uri).is_dir():
            raise FileNotFoundError(
                f"{LAKEHOUSE_URI_ENV}={self.uri!r} is not a readable lakehouse directory")

    @property
    def mode(self) -> str:
        return "live" if self.uri else "fixture"

    def extract(self, name: str) -> ExtractResult:
        if name not in EXPECTED_SCHEMAS:
            raise KeyError(f"unknown dataset {name!r}; known: {sorted(EXPECTED_SCHEMAS)}")
        if self.uri:
            df = self._extract_live(name)
            source = self.uri
        else:
            df = self._extract_fixture(name)
            source = "fixture:synthetic"
        validate_schema(name, df)
        return ExtractResult(name=name, df=df, source=source,
                             dataset_hash=synthetic.dataset_hash(df))

    def _extract_fixture(self, name: str) -> pd.DataFrame:
        gen = synthetic.GENERATORS[name]
        return gen(seed=self.seed)

    def _extract_live(self, name: str) -> pd.DataFrame:
        root = Path(self.uri)
        for cand in (root / "gold" / f"{name}.parquet", root / f"{name}.parquet",
                     root / "gold" / f"{name}.csv", root / f"{name}.csv"):
            if cand.exists():
                return pd.read_parquet(cand) if cand.suffix == ".parquet" else pd.read_csv(cand)
        raise FileNotFoundError(f"no gold extract for {name!r} under {root}")


def validate_schema(name: str, df: pd.DataFrame) -> None:
    """Fail-closed schema validation against the extraction contract."""
    missing = EXPECTED_SCHEMAS[name] - set(df.columns)
    if missing:
        raise ValueError(f"dataset {name!r} missing required columns: {sorted(missing)}")
    if len(df) == 0:
        raise ValueError(f"dataset {name!r} is empty")


def time_split(df: pd.DataFrame, ts_col: str, train_frac: float = 0.7,
               val_frac: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Time-based train/val/test split (no leakage across the cutoff)."""
    df = df.sort_values(ts_col).reset_index(drop=True)
    n = len(df)
    i_train, i_val = int(n * train_frac), int(n * (train_frac + val_frac))
    return df.iloc[:i_train], df.iloc[i_train:i_val], df.iloc[i_val:]


def random_split(df: pd.DataFrame, seed: int = 0, train_frac: float = 0.7,
                 val_frac: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Deterministic shuffled split for iid tabular datasets."""
    df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    n = len(df)
    i_train, i_val = int(n * train_frac), int(n * (train_frac + val_frac))
    return df.iloc[:i_train], df.iloc[i_train:i_val], df.iloc[i_val:]
