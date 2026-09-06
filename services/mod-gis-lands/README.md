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

## Run & test

```bash
pip install -r services/mod-gis-lands/requirements.txt
python3 -m pytest services/mod-gis-lands/                 # from repo root
cd services/mod-gis-lands && uvicorn lands_app.main:app --port 8001
```

Production persistence schema: `db/migrations/0001_cadastre.sql` + `db/migrations/0003_titling_and_luc.sql`.
