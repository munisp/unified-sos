# mod-agri-trace — Commodity Aggregation, Agro-Hub Warehouse Receipts & Crop Traceability

**National Edition blueprint · Commodity Exchanges, Agro-Allied Processors, Smallholder Farmers**

Aggregation intake for smallholder lots, signed/hash-chained agro-hub
**warehouse receipts** (WRs) usable as loan collateral, and end-to-end
**crop traceability** (farm → aggregation center → warehouse →
processor/export) with verifiable provenance chains.

- **Stack:** Python 3.11+ · FastAPI · pydantic v2 (reference build);
  production wiring: Mojaloop (settlement), Temporal (WR lifecycle), PostGIS (geo)
- **Pilot states:** Benue (yam) · Osun (cocoa) · Taraba (tea) · Kebbi (rice) · Kano (grains: sorghum/maize)

## Multi-tenancy

All state is scoped by the `X-State-Tenant` header (per-state isolation);
requests without it get **HTTP 400**. Tenant data is never shared across
states.

## Warehouse receipt lifecycle

```
issued --pledge--> pledged --release--> released --redeem--> redeemed
   |                   |
   +------redeem------>+----------------redeem----------------+
```

Illegal transitions (e.g. pledging a redeemed receipt, releasing an issued
one) return **HTTP 409**. Every lifecycle event is appended to a per-tenant
**hash chain** (`services/_shared/hashchain`, P1 audit immutability);
tampering, deletion, or reordering is detected by
`GET /agri/v1/receipt-chain/verify` and by trace lookups.

- **Title transfer** (`POST /agri/v1/receipts/{id}/transfer`) records a
  double-entry style ledger pair (DEBIT from-holder / CREDIT to-holder),
  itself hash-chained; title is not negotiable after redemption.
- **Storage fees** are integer **kobo** (NGN minor unit) — no floats.
- **Settlement hooks:** WR issue/pledge/redeem/release and trace hops emit
  intent events to the shared event bus (`_shared.eventbus`; Mojaloop seam):
  `ng.sos.agri.warehouse_receipt_issued`, `ng.sos.agri.receipt_pledged`,
  `ng.sos.agri.receipt_redeemed`, `ng.sos.agri.lot_traced_hop`.

## Traceability

Lot intake auto-records the `farm` hop (geo-validated against a Nigeria
bounding box; PostGIS-style); WR issuance records the `warehouse` hop.
Additional hops (`aggregation_center`, `processor`, `export`) are appended
via `POST /agri/v1/trace/hops`. `GET /agri/v1/trace/{lot_id}` returns the
full chain plus `chain_valid` / `errors` from hash verification.

## Adapter bindings (fail-closed)

Selected via `SOS_AGRI_PROFILE` (mirrors the mod-erp-bridge `build_adapter`
idiom):

| `SOS_AGRI_PROFILE` | Commodity exchange (price discovery) | Warehouse IoT (telemetry) |
| --- | --- | --- |
| unset / `fixture` / `local` / `test` (default) | `FixtureCommodityExchange` — deterministic kobo/kg prices | `FixtureWarehouseIoT` — in-memory readings |
| `production` / `live` | `HttpCommodityExchange` — requires `SOS_AGRI_EXCHANGE_URL` (LCFE/AFEX seam); **hard-fails at boot** (`AdapterUnavailableError`) without it | `HttpWarehouseIoT` — requires `SOS_AGRI_IOT_URL`; **hard-fails at boot** without it |

## Endpoints (all under `/agri/v1`, tenant-scoped)

| Endpoint | Purpose |
| --- | --- |
| `POST/GET /farmers`, `GET /farmers/{id}` | farmer/supplier registry (`kyc_ref` seam to mod-kyc-kyb) |
| `GET /commodities` | per-state catalog with fixture defaults |
| `POST/GET /lots` | aggregation intake (weight, grade A/B/C, moisture %, GPS origin) |
| `POST/GET /warehouses` | agro-hub warehouse registry |
| `POST /receipts` | issue a hash-chained WR for a stored lot |
| `GET /receipts`, `GET /receipts/{id}`, `GET /receipts/{id}/ledger` | WR queries + title ledger |
| `POST /receipts/{id}/pledge|release|redeem|transfer` | WR lifecycle + title transfer |
| `GET /receipt-chain/verify` | WR hash-chain integrity |
| `POST /trace/hops`, `GET /trace/{lot_id}` | provenance hops + full verified chain |
| `GET /prices/{commodity}` | exchange price discovery (adapter) |
| `POST /telemetry`, `GET /telemetry/{warehouse_id}` | IoT moisture/temperature ingest (adapter) |
| `GET /healthz`, `GET /metrics` | health + shared observability exposition |

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `SOS_AGRI_PROFILE` | `fixture` | adapter profile: `fixture`/`local`/`test` or `production`/`live` |
| `SOS_AGRI_EXCHANGE_URL` | — | commodity exchange base URL (required in production) |
| `SOS_AGRI_IOT_URL` | — | warehouse telemetry base URL (required in production) |
| `EVENT_BUS` | `memory` | shared event-bus selection (`_shared.eventbus`) |

## Run

```bash
pip install -e services/mod-agri-trace[dev]
uvicorn app.main:app --app-dir services/mod-agri-trace --port 8020
cd services/mod-agri-trace && python3 -m pytest -q
```

## State adoption (pilot lots)

| State | Anchor commodity | Role |
| --- | --- | --- |
| Benue | yam | aggregation + WR pilots |
| Osun | cocoa | export-grade traceability |
| Taraba | tea | highland aggregation centers |
| Kebbi | rice | paddy WR collateral |
| Kano | grains (sorghum/maize) | exchange-linked price discovery |
