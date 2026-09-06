# mod-education — Tertiary Consolidated Billing & Bursary

**WP-11 / EPIC-13 · Lot 7 · RT-04**

Unifies tuition, departmental levies, and accommodation fees across state universities and polytechnics with automated disbursement to institution accounts (e.g., UNIOSUN / State Polytechnic billing portal, course-registration lock).

- **Acceptance:** real-time student fee clearance; automated university-treasury ↔ state CRF reconciliation
- **Stack:** Go · PostgreSQL · Mojaloop · Keycloak
- **Deploys:** Osun (lead), all 6

## Reference implementation (Python/FastAPI)

`app/` provides the student billing portal (`GET /education/v1/students/{id}/billing`),
consolidated invoice issuance/payment (`/education/v1/invoices`), course
registration with a payment-status lock (`POST /education/v1/registrations` →
HTTP 423 while any invoice is outstanding, unlocking in real time on payment),
and a Mojaloop-clearing webhook stub (`POST /education/v1/webhooks/mojaloop`,
underpayments rejected with 422). Amounts are integer kobo; ledger posting
intent is TigerBeetle transfer code 150.

```bash
pip install -e services/mod-education[dev]
uvicorn app.main:app --app-dir services/mod-education --port 8011
cd services/mod-education && python3 -m pytest
```
