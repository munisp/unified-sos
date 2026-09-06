# mod-rev-core — Core Revenue & Automated Assessment Engine

**WP-05 / EPIC-05 · Lot 3 · RT-02**

Digitizes direct assessment, PAYE, withholding tax, consumption tax, and informal market daily levies across all state LGAs. Generates STINs linked to NIN/BVN/CAC; issues dynamic NIBSS e-Bills and QR codes; posts every kobo double-entry to TigerBeetle.

- **API contract:** [`contracts/openapi/revenue-assessments.yaml`](../../contracts/openapi/revenue-assessments.yaml)
- **Schema:** [`db/migrations/0002_revenue_core.sql`](../../db/migrations/0002_revenue_core.sql)
- **Config surface:** JSON policy files — tax brackets, reliefs, penalty rates, revenue heads (`config/states/<state>/`)
- **NFRs:** 99.999% availability; < 25 ms p99; 2,500 bill settlements/sec per state at peak
- **Acceptance:** 100,000 automated assessments with zero discrepancy vs gazetted tax laws; offline POS caches/signs 5,000 transactions
- **Stack:** Go · PostgreSQL (RLS) · Temporal · Redis · TigerBeetle
