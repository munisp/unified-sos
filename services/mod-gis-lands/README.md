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

Subdivision/merger require status `ACTIVE`/`REGISTERED`, no open dispute, and no RUNNING titling workflow; disputed parcels also block titling approvals (409).

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `SOS_LANDS_PROFILE` | `dev` | `production` enables fail-closed boot: missing seam URLs raise `AdapterUnavailableError` at startup |
| `SOS_LANDS_RISK_URL` | _(fixture scorer in dev)_ | Title-risk HTTP endpoint, e.g. `http://mod-ml-inference:8021/ml/v1/fraud/score`; **required in production** |
| `SOS_LANDS_ANCHOR_URL` | _(in-memory fixture anchor in dev)_ | Anchor/notary service URL; **required in production** |

## Run & test

```bash
pip install -r services/mod-gis-lands/requirements.txt
python3 -m pytest services/mod-gis-lands/                 # from repo root
cd services/mod-gis-lands && uvicorn lands_app.main:app --port 8001
```

Production persistence schema: `db/migrations/0001_cadastre.sql` + `db/migrations/0003_titling_and_luc.sql`.
