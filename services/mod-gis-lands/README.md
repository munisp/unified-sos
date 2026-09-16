# mod-gis-lands — Cadastral Land Administration & Titling

**WP-06 / EPIC-06 · Lot 4 · RT-03**

End-to-end digital land registry: parcel storage/validation (UTM Minna Datum EPSG:26391/26392/26393 + WGS84 EPSG:4326), topological overlap checking against gazetted reserves/setbacks/existing titles, multi-stage e-C-of-O approval via Temporal (`CadastralTitlingWorkflow`: Surveyor → Town Planning → Attorney General → Governor digital signature), cryptographically signed digital titles.

- **API contract:** [`contracts/openapi/cadastre-parcels.yaml`](../../contracts/openapi/cadastre-parcels.yaml)
- **Schema:** [`db/migrations/0001_cadastre.sql`](../../db/migrations/0001_cadastre.sql)
- **Acceptance:** C-of-O from 18 months to < 14 days; zero overlapping polygons; sub-meter precision
- **Stack:** PostGIS · Apache Sedona · Temporal · Python
- **State systems integrated:** NAGIS · BENGIS · TAGIS · LASGIS · OLARMS · OGIS

## Implementation

`lands_app/` implements the contract end-to-end, runnable and testable without live PostGIS/Temporal:

| Component | File | Notes |
|---|---|---|
| FastAPI service (contract endpoints + internal titling signals) | [lands_app/main.py](lands_app/main.py) | `registerParcel` (201/409), `searchParcels`, `verifyDeed` |
| Geometry validation (shapely + pyproj geodesic) | [lands_app/geometry.py](lands_app/geometry.py) | Mirrors `GEOMETRY(Polygon,4326)` + `trg_parcels_no_overlap`; overlap tolerance 0.01 m² (M4.2 sub-meter) |
| Persistence seam | [lands_app/repository.py](lands_app/repository.py) | `InMemoryParcelRepository` (tenant-isolated) + documented `PostGISParcelRepository` stub (RLS mapping) |
| e-C-of-O workflow (`CadastralTitlingWorkflow`) | [lands_app/titling.py](lands_app/titling.py) | Application → Surveyor → Town Planning → AG → Governor consent → issuance; activity/workflow separation; `LocalTitlingRunner` for tests |
| Temporal adapter | [lands_app/temporal_adapter.py](lands_app/temporal_adapter.py) | Production wiring notes (task queues per state, signals, durable SLA timers) |
| SLA clocks | [lands_app/sla.py](lands_app/sla.py) | Osun 45 d, Benue 60–90 d, Lagos consent SLA, Taraba TAGIS clearance; breach detection |
| Signed titles | [lands_app/signing.py](lands_app/signing.py) | Ed25519 (EdDSA) compact JWS; registry + governor-consent signature chain |
| Hash-chained cadastre event log | [lands_app/eventlog.py](lands_app/eventlog.py) | Append-only audit chain (`_shared.hashchain`); every mutation recorded |
| Subdivision & merger | [lands_app/subdivision.py](lands_app/subdivision.py) | Area conservation (0.5% tolerance, geodesic), parent `SUPERSEDED` (never deleted), `parent_parcel_ids` lineage, adjacency check for mergers |
| Dispute management | [lands_app/disputes.py](lands_app/disputes.py) | OPENED → UNDER_REVIEW → RESOLVED/DISMISSED; `DisputeGuard` freezes titling approvals + subdivision/merger on disputed parcels (409) |
| Chain-of-title history | [lands_app/history.py](lands_app/history.py) | Chronological owner/instrument/from-to/tx-reference entries from registry + workflow transitions + lineage events |
| Title-risk scoring seam | [lands_app/risk.py](lands_app/risk.py) | `FixtureTitleRiskScorer` (deterministic sha256 base + factors: open dispute +30, rapid transfers +15, lineage gaps +20); `HttpTitleRiskScorer` to mod-ml-inference |
| Title-hash anchoring seam | [lands_app/anchoring.py](lands_app/anchoring.py) | `FixtureAnchor` append-only merkle chain (`sha256(payload)‖prev`); anchor/verify with tamper detection; `HttpAnchorAdapter` notary seam |
| Transfer of ownership (`TransferWorkflow`) | [lands_app/transfers.py](lands_app/transfers.py) | SALE / GIFT / ASSENT / COURT_ORDER / FORECLOSURE_SALE / PARTITION; APPLICATION → EVIDENCE_VERIFICATION → CONSENT → TAX_CLEARANCE → REGISTERED; signed transfer JWS chained to previous title hash; `TitleRegistry` keeps replaced/revoked titles (never deleted) |
| Governor consent instruments | [lands_app/transfers.py](lands_app/transfers.py) | Single-use, expiring consent consumed at the CONSENT stage (SALE-class dealings) |
| Land-docs / tax adapter seams | [lands_app/legal_adapters.py](lands_app/legal_adapters.py) | Fail-closed `LandDocsAdapter` (evidence verification) + `TaxClearanceAdapter`; fixture defaults |
| Encumbrance register | [lands_app/encumbrances.py](lands_app/encumbrances.py) | MORTGAGE / CAVEAT / CAUTION / LIS_PENDENS / COURT_ORDER / LEASE; priority + instrument hash + expiry; release/withdraw workflow; `EncumbranceGuard` freezes titling/subdivision/merger/transfer (409) |
| Probate / transmission | [lands_app/succession.py](lands_app/succession.py) | DEATH_REPORTED (docs-verified certificate) → PROBATE_VERIFICATION → REGISTRAR_REVIEW → AG_REVIEW → TRANSMISSION_REGISTERED; multi-beneficiary; completes via TransferService (ASSENT) |
| Court orders | [lands_app/court_orders.py](lands_app/court_orders.py) | VEST_TITLE / RECTIFY_OWNER (via TransferService COURT_ORDER) / RECTIFY_BOUNDARY (pre-rectification snapshot in event log); registrar + AG approvals |
| Revocation + compensation | [lands_app/revocation.py](lands_app/revocation.py) | NOTICE_ISSUED → PUBLIC_PURPOSE_VERIFIED → COMPENSATION_ASSESSED (integer-kobo line items) → GOVERNOR_INSTRUMENT_SIGNED (Ed25519, chains to original title hash) → REVOCATION_EFFECTIVE; two-phase hold/post/void ledger (`SOS_LANDS_LEDGER_URL`) with deterministic ids |

## Extension endpoints

All tenant-scoped by path `state_id` **and** the `X-State-Tenant` header (400 when missing/mismatched; cross-tenant resources invisible → 404). Prometheus counter `lands_cadastre_operations_total{operation,outcome}` on each.

| Endpoint | Operation |
|---|---|
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/subdivide` | Split parcel into N children (area conservation, lineage, parent SUPERSEDED) |
| `POST /api/v1/states/{state_id}/cadastre/parcels/merge` | Merge 2+ adjacent parents into one child |
| `GET  /api/v1/states/{state_id}/cadastre/parcels/{id}/history` | Chain-of-title entries |
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/disputes` · `GET .../disputes` | Lodge / list disputes |
| `POST /api/v1/states/{state_id}/cadastre/disputes/{dispute_id}/review\|resolve\|dismiss` | Dispute lifecycle transitions |
| `GET  /api/v1/states/{state_id}/cadastre/parcels/{id}/risk` | Title-risk score + factors |
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/anchor` · `GET .../anchors/{anchor_id}/verify` | Anchor title hash / verify anchor |
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/encumbrances` · `GET .../encumbrances` | Register / list encumbrances |
| `POST /api/v1/states/{state_id}/cadastre/encumbrances/{encumbrance_id}/release\|withdraw` | Encumbrance discharge lifecycle |
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/transfers` · `GET .../transfers/{transfer_id}` · `POST .../transfers/{transfer_id}/advance` | Transfer dealings (ownership change) |
| `POST /api/v1/states/{state_id}/cadastre/consents` · `GET .../consents/{consent_id}` | Governor consent instruments (single-use, expiring) |
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/transmissions` · `POST .../transmissions/{id}/advance` | Probate / transmission to beneficiaries |
| `POST /api/v1/states/{state_id}/cadastre/court-orders` · `POST .../court-orders/{id}/approve\|apply` | Court-ordered vesting / rectification |
| `POST /api/v1/states/{state_id}/cadastre/parcels/{id}/revocations` · `POST .../revocations/{id}/advance` | Public-purpose revocation + compensation |

Subdivision/merger require status `ACTIVE`/`REGISTERED`, no open dispute, no active encumbrance, and no RUNNING titling workflow; children **always inherit the parent owner** (the deprecated `owner_stin` override is rejected with 422, as is a merger of differently-owned parents — change ownership via a registered transfer first). `verifyDeed` is lifecycle-aware: SUPERSEDED/REVOKED parcels and REPLACED/REVOKED titles return `valid=false` with the parcel status and a replacement/revocation reference. Titling decisions enforce segregation of duties: a distinct actor per stage with role mapping registry → surveyor → ministry (`town-planning`) → AG (`attorney-general`) → governor (actor prefix before `:` must match the stage role; violations → 409).

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `SOS_LANDS_PROFILE` | `dev` | `production` enables fail-closed boot: missing seam URLs raise `AdapterUnavailableError` at startup |
| `SOS_LANDS_RISK_URL` | _(fixture scorer in dev)_ | Title-risk HTTP endpoint, e.g. `http://mod-ml-inference:8021/ml/v1/fraud/score`; **required in production** |
| `SOS_LANDS_ANCHOR_URL` | _(in-memory fixture anchor in dev)_ | Anchor/notary service URL; **required in production** |
| `SOS_LANDS_DOCS_URL` | _(fixture adapter in dev)_ | mod-land-docs evidence verification endpoint; **required in production** |
| `SOS_LANDS_TAX_URL` | _(fixture adapter in dev)_ | Tax clearance endpoint for transfers; **required in production** |
| `SOS_LANDS_LEDGER_URL` | _(fixture ledger in dev)_ | Compensation ledger (hold/post/void); **required in production** |

## Run & test

```bash
pip install -r services/mod-gis-lands/requirements.txt
python3 -m pytest services/mod-gis-lands/                 # from repo root
cd services/mod-gis-lands && uvicorn lands_app.main:app --port 8001
```

Production persistence schema: `db/migrations/0001_cadastre.sql` + `db/migrations/0003_titling_and_luc.sql`.
