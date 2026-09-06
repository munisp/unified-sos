"""Tests for the medallion pipeline over local fixtures."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lakehouse.medallion import (
    SILVER_COLUMNS,
    daily_igr_by_state,
    delinquency_scores,
    ingest_raw_events,
    normalize_events,
    read_parquet,
    write_parquet,
)


def test_bronze_ingest_preserves_raw_payload(raw_fixture: Path) -> None:
    bronze = ingest_raw_events(raw_fixture)
    assert len(bronze) == 8
    assert "_landed_at" in bronze.columns  # landing metadata added
    # Raw aliases preserved verbatim at bronze.
    assert "amt_kobo" in bronze.columns or "amount" in bronze.columns


def test_silver_normalization(raw_fixture: Path) -> None:
    silver = normalize_events(ingest_raw_events(raw_fixture))
    assert list(silver.columns) == SILVER_COLUMNS
    # 8 raw - 1 duplicate - 1 negative - 1 missing state = 5 silver rows.
    assert len(silver) == 5
    assert silver["event_id"].is_unique
    assert set(silver["tenant_state_id"]) == {"lagos", "ogun"}  # lowercased
    assert (silver["amount_kobo"] >= 0).all()
    assert silver["amount_kobo"].dtype == "int64"
    # Aliases unified.
    assert silver.loc[silver.event_id == "evt-003", "amount_kobo"].iloc[0] == 500000
    # Channel inference: device_id → iot.
    assert silver.loc[silver.event_id == "evt-004", "channel"].iloc[0] == "iot"
    assert silver.loc[silver.event_id == "evt-001", "channel"].iloc[0] == "pos"
    # Timestamps parsed to UTC.
    assert str(silver["event_ts"].dt.tz) == "UTC"


def test_silver_roundtrip_via_parquet(raw_fixture: Path, tmp_path: Path) -> None:
    silver = normalize_events(ingest_raw_events(raw_fixture))
    path = write_parquet(silver, tmp_path / "silver" / "revenue_events.parquet")
    loaded = read_parquet(path)
    # The JSON fallback (no parquet engine installed) does not preserve
    # datetime dtypes; parquet roundtrips are checked strictly.
    pd.testing.assert_frame_equal(
        silver, loaded, check_dtype=path.suffix == ".parquet"
    )


def test_gold_daily_igr_aggregation(raw_fixture: Path) -> None:
    silver = normalize_events(ingest_raw_events(raw_fixture))
    gold = daily_igr_by_state(silver)
    lagos_d1 = gold[(gold.tenant_state_id == "lagos")
                    & (gold.day.astype(str) == "2026-07-01")].iloc[0]
    assert lagos_d1["receipts"] == 2
    assert lagos_d1["total_kobo"] == 400000
    lagos_d2 = gold[(gold.tenant_state_id == "lagos")
                    & (gold.day.astype(str) == "2026-07-02")].iloc[0]
    assert lagos_d2["total_kobo"] == 500000
    ogun_total = gold[gold.tenant_state_id == "ogun"]["total_kobo"].sum()
    assert ogun_total == 155000


def test_gold_delinquency_scoring_stub(raw_fixture: Path) -> None:
    silver = normalize_events(ingest_raw_events(raw_fixture))
    scores = delinquency_scores(silver, as_of=pd.Timestamp("2026-07-31", tz="UTC"))
    # payer-a last paid 2026-07-02T08:40Z → 28 full days → 28/30 ≈ 0.9333
    pa = scores[scores.payer_ref == "payer-a"].iloc[0]
    assert pa["days_since_last_payment"] == 28
    assert abs(pa["delinquency_score"] - round(28 / 30, 4)) < 1e-9
    # payer-b last paid 2026-07-01T10:02Z → 29 full days → 29/30
    pb = scores[scores.payer_ref == "payer-b"].iloc[0]
    assert pb["delinquency_score"] == round(29 / 30, 4)
    # Cap: a payer long overdue saturates at 1.0.
    capped = delinquency_scores(silver[silver.payer_ref == "payer-b"],
                                as_of=pd.Timestamp("2027-07-01", tz="UTC"))
    assert capped.iloc[0]["delinquency_score"] == 1.0
    # Deterministic ordering: state asc, score desc.
    assert list(scores.tenant_state_id.unique()) == ["lagos", "ogun"]


def test_empty_silver_gold_contract() -> None:
    empty = pd.DataFrame(columns=SILVER_COLUMNS)
    gold = daily_igr_by_state(empty)
    assert list(gold.columns) == ["tenant_state_id", "day", "receipts",
                                  "total_kobo", "avg_kobo"]
    scores = delinquency_scores(empty, as_of=pd.Timestamp("2026-07-31", tz="UTC"))
    assert "delinquency_score" in scores.columns
