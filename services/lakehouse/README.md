# lakehouse — Data Platform, Streaming & AI/ML

**WP-15 / EPIC-17/18 · Lot 7 · RT-05**

Medallion lakehouse (Bronze/Silver/Gold Delta Lake on MinIO), Flink real-time revenue windowing, DataFusion Gold-table query service, Ray distributed ML: Automated Property Valuation (XGBoost on Ray) and tax-evasion anomaly scoring (isolation forests, graph NNs).

- **Architecture:** [`docs/architecture/05-lakehouse-ai.md`](../../docs/architecture/05-lakehouse-ai.md)
- **Acceptance:** streaming ingest < 2 s with zero loss; sub-second Gold queries; leakage alerts < 60 s; AVM > 92% R²
- **Stack:** Delta Lake · Flink · Spark · DataFusion · Ray · MinIO · MLflow
- **Deploys:** all 6 (federated; Lagos/Ogun dedicated compute slices)

## Reference implementation (`lakehouse/medallion.py`, pandas)

Bronze → Silver → Gold as pure, deterministic functions over local
parquet/JSON fixtures:

| Layer | Function | Production mapping |
|---|---|---|
| **Bronze** | `ingest_raw_events(path)` — raw POS receipt / IoT ingestion JSON landed verbatim + `_landed_at` | Flink/Spark Structured Streaming append to Delta `bronze.revenue_events` on MinIO (streaming ingest < 2 s, zero loss) |
| **Silver** | `normalize_events(bronze_df)` — alias unification (`amt_kobo`/`amount`, `ts`/`timestamp`), channel inference (`device_id` → `iot`), dedupe on `event_id`, quarantine of negative/malformed rows, UTC timestamps, canonical `SILVER_COLUMNS` schema | Flink bronze→silver job writing Delta `silver.revenue_events`; quarantined rows route to a dead-letter Delta table |
| **Gold** | `daily_igr_by_state(silver_df)` — per-state per-day IGR totals (receipts, total/avg kobo), deterministic ordering | Spark batch / Flink windowing into Delta `gold.igr_daily_state`, served sub-second by DataFusion |
| **Gold (ML stub)** | `delinquency_scores(silver_df, as_of)` — per-payer score in [0,1]: fraction of the expected 30-day remittance interval elapsed since last payment (capped) | Ray isolation-forest / graph-NN anomaly scoring; the per-payer sorted score frame is the stable interface |

```bash
pip install -e services/lakehouse[dev]
cd services/lakehouse && python3 -m pytest
```

Fixtures are generated deterministically in `tests/conftest.py` (POS receipts
for Lagos, WIM IoT sensor events for Ogun, plus duplicate/quarantine cases).
