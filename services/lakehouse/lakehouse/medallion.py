"""Medallion pipeline: bronze → silver → gold as pure pandas functions.

Layers
------
bronze  Raw POS receipt / IoT ingestion events, as-landed (JSON/parquet).
silver  Normalized, deduplicated, typed revenue events.
gold    Per-state daily IGR aggregation + delinquency scoring (stub).

All functions are pure: no globals, no I/O beyond explicitly passed paths.
Production mapping: Delta Lake tables on MinIO, Flink for the streaming
bronze→silver hop, Ray for gold-layer ML feature builds (see README).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

#: Silver-layer canonical schema (column order is part of the contract).
SILVER_COLUMNS = [
    "event_id", "tenant_state_id", "revenue_head", "channel",
    "amount_kobo", "payer_ref", "event_ts", "ingest_ts",
]

VALID_CHANNELS = ("pos", "iot")


# ---------------------------------------------------------------- bronze ---
def ingest_raw_events(raw_json_path: Path) -> pd.DataFrame:
    """Bronze: load raw POS receipt / IoT ingestion JSON as-landed.

    No normalization here — bronze preserves the source payload verbatim plus
    a landing timestamp column.
    """
    rows = json.loads(Path(raw_json_path).read_text())
    if not isinstance(rows, list):
        raise ValueError("raw fixture must be a JSON array of event objects")
    df = pd.DataFrame(rows)
    df["_landed_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    return df


def _parquet_engine_available() -> bool:
    try:
        import pyarrow.parquet  # noqa: F401

        return True
    except ImportError:
        try:
            import fastparquet  # noqa: F401

            return True
        except ImportError:
            return False


def write_parquet(df: pd.DataFrame, path: Path) -> Path:
    """Persist a layer to parquet (local stand-in for a Delta table).

    Falls back to JSON records when no parquet engine (pyarrow/fastparquet)
    is installed, so the reference pipeline stays runnable in minimal
    environments. Production writes Delta tables via Spark/Flink — see README.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _parquet_engine_available():
        df.to_parquet(path, index=False)
        return path
    fallback = path.with_suffix(".json")
    df.to_json(fallback, orient="records")
    return fallback


def read_parquet(path: Path) -> pd.DataFrame:
    path = Path(path)
    # JSON fallback is a layer-format stand-in, not a schema break: parse the
    # canonical timestamp columns back to datetimes so silver frames compare
    # equal whether the engine wrote parquet or JSON records.
    date_cols = ["event_ts"]
    if path.suffix == ".json" and path.exists():
        return pd.read_json(path, orient="records", convert_dates=date_cols)
    if path.exists() and _parquet_engine_available():
        return pd.read_parquet(path)
    fallback = path.with_suffix(".json")
    if fallback.exists():
        return pd.read_json(fallback, orient="records", convert_dates=date_cols)
    if path.exists():
        return pd.read_parquet(path)
    raise FileNotFoundError(f"neither {path} nor {fallback} exists")


# ---------------------------------------------------------------- silver ---
def normalize_events(bronze: pd.DataFrame) -> pd.DataFrame:
    """Silver: normalize raw POS/IoT events into the canonical revenue schema.

    Rules:
    - field aliases unified (``amt_kobo``/``amount``/``amount_kobo`` → amount_kobo;
      ``ts``/``timestamp``/``event_ts`` → event_ts parsed to UTC);
    - channel inferred: records with ``device_id`` are IoT, else POS;
    - duplicates on ``event_id`` dropped (first wins);
    - rows with missing event_id/state or negative amounts are quarantined
      (dropped — production routes them to a dead-letter Delta table);
    - state ids lowercased; amounts coerced to int64 kobo.
    """
    df = bronze.copy()

    # Unify amount aliases.
    for alias in ("amt_kobo", "amount"):
        if alias in df.columns:
            df["amount_kobo"] = df.get("amount_kobo", pd.Series(dtype="object")).fillna(df[alias])
    # Unify timestamp aliases.
    for alias in ("ts", "timestamp"):
        if alias in df.columns:
            df["event_ts"] = df.get("event_ts", pd.Series(dtype="object")).fillna(df[alias])

    # Channel inference.
    if "channel" not in df.columns:
        df["channel"] = pd.NA
    df["channel"] = df["channel"].fillna(
        df.get("device_id", pd.Series(pd.NA, index=df.index)).notna().map(
            {True: "iot", False: "pos"})
    )

    required = {"event_id", "tenant_state_id", "revenue_head", "amount_kobo",
                "payer_ref", "event_ts", "ingest_ts"}
    for col in required:
        if col not in df.columns:
            df[col] = pd.NA

    df = df.dropna(subset=["event_id", "tenant_state_id", "amount_kobo"])
    df["tenant_state_id"] = df["tenant_state_id"].astype(str).str.lower()
    df["channel"] = df["channel"].astype(str).str.lower()
    df = df[df["channel"].isin(VALID_CHANNELS)]
    df["amount_kobo"] = pd.to_numeric(df["amount_kobo"], errors="coerce")
    df = df.dropna(subset=["amount_kobo"])
    df = df[df["amount_kobo"] >= 0]
    df["amount_kobo"] = df["amount_kobo"].astype("int64")
    df["event_ts"] = pd.to_datetime(df["event_ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["event_ts"])
    df = df.drop_duplicates(subset=["event_id"], keep="first")
    return df[SILVER_COLUMNS].sort_values("event_ts").reset_index(drop=True)


# ------------------------------------------------------------------ gold ---
def daily_igr_by_state(silver: pd.DataFrame) -> pd.DataFrame:
    """Gold: per-state, per-day IGR aggregation.

    Returns columns: tenant_state_id, day (date), receipts, total_kobo,
    avg_kobo — sorted by state/day for deterministic output.
    """
    if silver.empty:
        return pd.DataFrame(columns=["tenant_state_id", "day", "receipts",
                                     "total_kobo", "avg_kobo"])
    df = silver.assign(day=silver["event_ts"].dt.date)
    gold = (
        df.groupby(["tenant_state_id", "day"], as_index=False)
        .agg(receipts=("event_id", "count"),
             total_kobo=("amount_kobo", "sum"),
             avg_kobo=("amount_kobo", "mean"))
        .sort_values(["tenant_state_id", "day"])
        .reset_index(drop=True)
    )
    gold["avg_kobo"] = gold["avg_kobo"].round().astype("int64")
    return gold


def delinquency_scores(silver: pd.DataFrame, as_of: pd.Timestamp,
                       expected_interval_days: int = 30) -> pd.DataFrame:
    """Gold: delinquency scoring stub per (state, payer).

    Score in [0, 1]: fraction of the expected payment interval elapsed since
    the payer's last remittance (capped at 1.0). Production replaces this with
    the isolation-forest / graph-NN anomaly models on Ray — the interface
    (per-payer score frame, sorted, deterministic) is the contract.
    """
    as_of = pd.Timestamp(as_of, tz="UTC") if pd.Timestamp(as_of).tzinfo is None \
        else pd.Timestamp(as_of)
    cols = ["tenant_state_id", "payer_ref", "last_payment_ts",
            "days_since_last_payment", "delinquency_score"]
    if silver.empty:
        return pd.DataFrame(columns=cols)
    last = (
        silver.groupby(["tenant_state_id", "payer_ref"], as_index=False)
        .agg(last_payment_ts=("event_ts", "max"))
    )
    last["days_since_last_payment"] = (
        (as_of - last["last_payment_ts"]).dt.total_seconds() // 86400
    ).astype("int64")
    last["delinquency_score"] = (
        last["days_since_last_payment"] / expected_interval_days
    ).clip(lower=0.0, upper=1.0).round(4)
    return last[cols].sort_values(
        ["tenant_state_id", "delinquency_score"],
        ascending=[True, False]).reset_index(drop=True)
