# mod-health — Public Health Billing & Facility Operations

**WP-10 / EPIC-12 · Lot 7 · RT-04**

Consolidated revenue collection across state tertiary and specialist hospitals, FHIR-compliant patient billing, drug inventory POS (revolving drug fund), and automated SHIA/NHIS insurance claims.

- **Acceptance:** 100% electronic patient fee collection (cash diversion eliminated); claims adjudicated < 24 h
- **Stack:** Go · PostgreSQL (RLS) · Temporal · Dapr · Keycloak
- **Deploys:** Nasarawa, Osun, Benue — extensible to all 6

## Reference implementation (Python/FastAPI)

`app/` provides: patient billing accounts (`POST /health/v1/billing-accounts`),
service/fee invoice issuance and payment (`/health/v1/invoices`), the SHIA/NHIS
claim record lifecycle (`submitted → adjudicated → paid | rejected` via
`/health/v1/claims*`), and a pharmacy stock-out–aware drug billing hook —
`DRUG:<code>` invoice lines atomically reserve facility stock and return
HTTP 409 with a `stock_out` payload when insufficient. Amounts are integer
kobo; ledger posting intent is TigerBeetle transfer code 150.

```bash
pip install -e services/mod-health[dev]
uvicorn app.main:app --app-dir services/mod-health --port 8010
cd services/mod-health && python3 -m pytest
```
