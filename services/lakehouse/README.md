# lakehouse — Data Platform, Streaming & AI/ML

**WP-15 / EPIC-17/18 · Lot 7 · RT-05**

Medallion lakehouse (Bronze/Silver/Gold Delta Lake on MinIO), Flink real-time revenue windowing, DataFusion Gold-table query service, Ray distributed ML: Automated Property Valuation (XGBoost on Ray) and tax-evasion anomaly scoring (isolation forests, graph NNs).

- **Architecture:** [`docs/architecture/05-lakehouse-ai.md`](../../docs/architecture/05-lakehouse-ai.md)
- **Acceptance:** streaming ingest < 2 s with zero loss; sub-second Gold queries; leakage alerts < 60 s; AVM > 92% R²
- **Stack:** Delta Lake · Flink · Spark · DataFusion · Ray · MinIO · MLflow
- **Deploys:** all 6 (federated; Lagos/Ogun dedicated compute slices)
