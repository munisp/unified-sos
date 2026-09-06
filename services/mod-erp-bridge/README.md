# mod-erp-bridge — ERP Integration Bridge

Tenant-isolated bridge between the SOS platform and state ERP / public
financial management systems. Balanced double-entry journal entries (integer
kobo) are ingested from the API — or automatically from
`ng.sos.payments.settlement_completed` settlement events on the shared event
bus — mapped from the state chart of accounts (COA) to ERP account codes,
pushed to the configured backend, and recorded in a hash-chained outbound
audit log (`services/_shared/hashchain.py`).

## Backends (fail-closed adapter idiom)

| `ERP_BACKEND` | Adapter | Required env | Notes |
|---|---|---|---|
| `local` (default) | `FixtureErpAdapter` | — | Deterministic receipts keyed by entry hash. |
| `ifmis_export` | `IfmisExportAdapter` | `IFMIS_EXPORT_DIR` (default `/tmp/ifmis-export`) | GIFMIS/IFMIS-style deterministic CSV+JSON export per journal; always available locally. |
| `odoo` | `OdooAdapter` | `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_API_KEY` | Stdlib `xmlrpc.client`: authenticate, create + post `account.move`. **Fails closed** at boot without all four vars. |
| `erpnext` | `ErpNextAdapter` | `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` | REST: creates + submits a `Journal Entry` with the `accounts` child table. **Fails closed** without all three vars. |

Any other `ERP_BACKEND` value raises `AdapterUnavailableError` at boot.

## Event subscription

When `EVENT_BUS=kafka` (with `EVENT_KAFKA_BOOTSTRAP`), settlement events are
consumed from the AsyncAPI topic registry; the default in-memory bus and
API-only ingestion are used otherwise. Subscription failures degrade to
API-only ingestion (never crash the service).

## Reliability

- **Dedupe**: `source_event_id` is unique per tenant; replays return a
  `DEDUPED` outbound record without re-pushing.
- **Retry**: push failures enter a bounded retry queue with exponential
  backoff (`max_attempts=3` default) and land in the dead-letter queue after
  exhaustion.
- **Integrity**: every outbound event (push, dedupe, dead-letter) is
  hash-chained; `GET .../outbound-log/verify` recomputes the chain.

## API (tenant-scoped; unknown state -> 404)

| Method | Path | Purpose |
|---|---|---|
| POST | `/erp/v1/states/{state}/journals` | Push a balanced journal entry |
| GET | `/erp/v1/states/{state}/journals` | List entries for the state |
| GET | `/erp/v1/states/{state}/journals/{id}` | Entry incl. ERP receipt |
| GET | `/erp/v1/states/{state}/coa-mapping` | Effective state COA -> ERP map |
| PUT | `/erp/v1/states/{state}/coa-mapping` | Replace state COA mapping |
| GET | `/erp/v1/states/{state}/outbound-log/verify` | Recompute outbound hash chain |
| GET | `/healthz` | Liveness + adapter health |

COA mapping precedence: in-repo default map, overridden by
`config/states/<state>/erp-coa.yaml` (key `coa_mapping:`), overridden at
runtime by `PUT .../coa-mapping`.

## Tests

```
cd services/mod-erp-bridge && python3 -m pytest tests -q
```
