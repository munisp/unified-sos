"""Fixtures: raw POS receipt / IoT ingestion JSON, generated deterministically."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RAW_EVENTS = [
    # POS receipts — Lagos, two days
    {"event_id": "evt-001", "tenant_state_id": "LAGOS", "revenue_head": "REV_MARKET",
     "amount_kobo": 150000, "payer_ref": "payer-a", "event_ts": "2026-07-01T09:15:00Z",
     "ingest_ts": "2026-07-01T09:15:04Z", "channel": "pos"},
    {"event_id": "evt-002", "tenant_state_id": "lagos", "revenue_head": "REV_MARKET",
     "amount_kobo": 250000, "payer_ref": "payer-b", "event_ts": "2026-07-01T10:02:00Z",
     "ingest_ts": "2026-07-01T10:02:01Z", "channel": "pos"},
    {"event_id": "evt-003", "tenant_state_id": "lagos", "revenue_head": "REV_LUC",
     "amt_kobo": 500000, "payer_ref": "payer-a", "ts": "2026-07-02T08:40:00Z",
     "ingest_ts": "2026-07-02T08:40:02Z"},
    # IoT ingestion (e.g. WIM weigh-in-motion levy sensor) — Ogun
    {"event_id": "evt-004", "tenant_state_id": "Ogun", "revenue_head": "REV_WIM",
     "amount": 75000, "payer_ref": "payer-c", "timestamp": "2026-07-01T11:30:00Z",
     "ingest_ts": "2026-07-01T11:30:01Z", "device_id": "wim-ogun-17"},
    {"event_id": "evt-005", "tenant_state_id": "ogun", "revenue_head": "REV_WIM",
     "amount_kobo": 80000, "payer_ref": "payer-c", "event_ts": "2026-07-02T11:31:00Z",
     "ingest_ts": "2026-07-02T11:31:01Z", "device_id": "wim-ogun-17"},
    # Duplicate event (idempotent replay) — must be dropped in silver
    {"event_id": "evt-002", "tenant_state_id": "lagos", "revenue_head": "REV_MARKET",
     "amount_kobo": 250000, "payer_ref": "payer-b", "event_ts": "2026-07-01T10:02:00Z",
     "ingest_ts": "2026-07-01T10:05:00Z", "channel": "pos"},
    # Quarantine candidates: negative amount, missing state
    {"event_id": "evt-006", "tenant_state_id": "lagos", "revenue_head": "REV_MARKET",
     "amount_kobo": -1000, "payer_ref": "payer-x", "event_ts": "2026-07-01T12:00:00Z",
     "ingest_ts": "2026-07-01T12:00:01Z", "channel": "pos"},
    {"event_id": "evt-007", "revenue_head": "REV_MARKET", "amount_kobo": 1000,
     "payer_ref": "payer-y", "event_ts": "2026-07-01T12:05:00Z",
     "ingest_ts": "2026-07-01T12:05:01Z", "channel": "pos"},
]


@pytest.fixture()
def raw_fixture(tmp_path: Path) -> Path:
    p = tmp_path / "bronze_raw.json"
    p.write_text(json.dumps(RAW_EVENTS))
    return p
