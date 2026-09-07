# mod-waterways — Inland Waterways Ferry E-Ticketing & Sand-Dredging Volumetric Monitoring

**National Edition blueprint module** — Inland Waterway Authorities (LASWA, NIWA liaison), Sand Dredgers.

Two capabilities:

1. **Ferry e-ticketing** — route/jetty registry per state, scheduled trips
   (route, vessel, capacity, departure), ticket sales with integer-kobo fares
   from a per-state fare policy (base fare + per-km rate), seat-capacity
   enforcement (HTTP 409 when sold out), QR-style ticket refs presented at
   boarding, and per-trip manifest generation. **Safety rule:** the manifest
   locks at departure — no further sales, no double departure (409).
2. **Sand-dredging volumetric monitoring** — dredger/vessel registry
   (license no + operator KYB reference from mod-kyc-kyb), volumetric survey
   ingest (dredger id, geo polygon, `volume_m3`, timestamp) with polygon
   shape/geofence validation, monthly cumulative volume per dredger vs the
   licensed quota → over-quota alert, and royalty levy computation (integer
   kobo per m³ from the state policy). Every royalty assessment is recorded
   in a **hash-chained audit log** (`services/_shared/hashchain.py`) with a
   tamper-check exposed on the royalties feed.

Published events: `ng.sos.waterways.ticket_sold`,
`ng.sos.waterways.dredging_volume_alert`, `ng.sos.waterways.royalty_assessed`
(`_shared.eventbus` InMemory bus in the reference build).

- **Stack:** PostGIS · Apache Sedona · TigerBeetle (production bindings; the
  reference build is in-memory with fail-closed adapter seams)
- **Adoption states:** Lagos, Bayelsa, Rivers, Benue, Delta, Kogi, Niger.
  Lagos routes are operated with **LASWA**; all other states run under the
  state Inland Waterway Authority in **NIWA liaison** (NIWA retains federal
  navigable-water jurisdiction — the state tenant model mirrors the
  operational data-sharing MOU, not a transfer of regulatory authority).
- **Tenancy:** every domain endpoint requires the `X-State-Tenant` header
  (HTTP 400 when missing or not an adoption state); cross-tenant access to
  trips/dredgers is HTTP 403 (fail-closed, matching mod-ppp-investment /
  mod-police-cad).

## Fixture defaults

Routes (per state): lagos → Ikorodu–CMS (24 km), Badagry–Marina (55 km);
bayelsa → Yenagoa–Brass; rivers → Port Harcourt–Bonny; benue → Makurdi–Gboko;
delta → Warri–Escravos; kogi → Lokoja–Idah; niger → Baro–Jebba.

Per-state fare policy: `base_fare_kobo + per_km_kobo × distance_km`
(integer kobo only); per-state `royalty_kobo_per_m3` for the dredging levy.

## Adapter bindings (fail-closed)

Sedona volumetrics and AIS telemetry are bound behind fail-closed adapters
with deterministic fixtures (idiom mirrors mod-erp-bridge / mod-police-cad).
Selection via `SOS_WATERWAYS_PROFILE`:

| `SOS_WATERWAYS_PROFILE` | Sedona volumetrics | AIS telemetry |
| --- | --- | --- |
| unset / `fixture` / `local` / `test` (default) | `FixtureSedonaAdapter` — deterministic volume = polygon area (shoelace, equirectangular-scaled) × depth | `FixtureAisAdapter` — deterministic position derived from the MMSI hash |
| `production` / `live` | `HttpSedonaAdapter` — POSTs to `SOS_WATERWAYS_SEDONA_URL`; **hard-fails at boot** (`AdapterUnavailableError`) without it | `HttpAisAdapter` — GETs `SOS_WATERWAYS_AIS_URL`; **hard-fails at boot** without it |

Any unknown profile value also fails closed.

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `SOS_WATERWAYS_PROFILE` | `fixture` \| `production` adapter selection | `fixture` |
| `SOS_WATERWAYS_SEDONA_URL` | Sedona volumetric verification endpoint (production) | — |
| `SOS_WATERWAYS_AIS_URL` | AIS aggregator endpoint (production) | — |
| `EVENT_BUS` / `EVENT_KAFKA_BOOTSTRAP` | shared event-bus selection (`_shared.eventbus`) | `memory` |

## Endpoints (all tenant-scoped unless noted)

- `GET /healthz`, `GET /metrics` — shared `_shared.observability` exposition
- `GET /waterways/v1/routes`, `GET /waterways/v1/jetties`, `GET /waterways/v1/fare-policy`
- `POST /waterways/v1/trips`, `GET /waterways/v1/trips`
- `POST /waterways/v1/tickets`, `GET /waterways/v1/tickets[?trip_id=…]`
- `GET /waterways/v1/trips/{trip_id}/manifest`, `POST /waterways/v1/trips/{trip_id}/depart`
- `POST /waterways/v1/dredgers`, `GET /waterways/v1/dredgers`
- `GET /waterways/v1/dredgers/{dredger_id}/position?mmsi=…` (AIS adapter)
- `POST /waterways/v1/surveys`, `GET /waterways/v1/surveys`
- `GET /waterways/v1/quotas[?month=YYYY-MM]`
- `GET /waterways/v1/royalties` — hash-chained audit feed with `chain_errors` tamper check

## Reference implementation (Python/FastAPI)

```bash
pip install -e services/mod-waterways[dev]
uvicorn app.main:app --app-dir services/mod-waterways --port 8000
cd services/mod-waterways && python3 -m pytest
```

## Container

```bash
docker build -t sos-mod-waterways services/mod-waterways
docker run -p 8000:8000 sos-mod-waterways
```

`python:3.12-slim`, non-root `sos` user, uvicorn on port 8000. The minimal
image ships only `app/`; `_shared` imports are guarded and the service falls
back to built-in fixture/hash implementations when the shared package is not
present.
