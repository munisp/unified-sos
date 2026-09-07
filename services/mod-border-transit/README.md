# mod-border-transit — Cross-Border Cargo RFID Tracking & Transit Telematics

**National Edition blueprint · Reference implementation (Python/FastAPI)**

Border crossing registry, RFID-tracked transit consignments
(declared → sealed → in_transit → arrived → cleared), corridor geofencing with
GPS telematics, tamper alerting (broken seals, route deviation, geofence
deviation), and per-state transit levy assessment recorded as ledger-style
double-entry intents.

- **Users:** State Border Development Agencies, Customs Liaisons, Freight Traders
- **Blueprint stack:** Rust · Fluvio · PostGIS · TigerBeetle (reference build:
  FastAPI + in-memory store, shared event bus, hash-chained audit)
- **Adoption states:** Taraba (Gembu–Cameroon, Ibi) · Borno (Gamboru–Chad,
  Banki) · Katsina (Jibia–Niger, Daura) · Sokoto (Illela–Niger) · Ogun
  (Idiroko–Benin) · Cross River (Mfum–Cameroon)

## Multi-tenancy

All endpoints are scoped by the `X-State-Tenant` header (HTTP 400 when
missing). Cross-tenant access to another state's crossings/consignments fails
closed with HTTP 403.

## Domain behaviour

- **Crossings CRUD** — fixture defaults per adoption state; tenants can add,
  patch, and delete crossings.
- **Consignment lifecycle** — invalid transitions return HTTP 409. The first
  RFID scan after sealing auto-advances `sealed → in_transit`.
- **RFID scans** — checkpoint scan events (tag, checkpoint, direction, geo,
  seal state) append to the consignment. A broken seal or a scan off the
  declared ordered corridor (or out of order) raises a tamper alert.
- **Telematics** — GPS pings are ray-cast against the per-state corridor
  geofence polygon; out-of-corridor pings raise `geofence_deviation` alerts.
- **Transit levy** — per-state policy (`flat_fee_kobo` + `ad_valorem_bps`),
  integer-kobo arithmetic (`value * bps // 10_000`, floored). Assessments
  carry a double-entry ledger intent (debit `trader:<ref>`, credit
  `state:<tenant>:transit_levy_revenue`) ready for TigerBeetle.
- **Audit** — clearance decisions form a SHA-256 hash chain
  (`services/_shared/hashchain`); `GET /border/v1/clearance-audit` reports
  chain integrity errors.
- **Events** — publishes `ng.sos.border.transit_crossing_recorded`,
  `ng.sos.border.tamper_alert`, `ng.sos.border.levy_assessed` on the shared
  event bus (`InMemoryEventBus` default; Kafka/Fluvio via `EVENT_BUS`).

## Fail-closed adapters (`app/adapters.py`)

Selection idiom mirrors mod-police-cad `build_webrtc_gateway`:

| Adapter | Default (fixture) | Live seam env var |
| --- | --- | --- |
| `RfidReaderAdapter` | deterministic canned scans | `SOS_BORDER_RFID_URL` |
| `TelematicsAdapter` | deterministic canned pings | `SOS_BORDER_TELEMATICS_URL` |

`SOS_BORDER_PROFILE=production` **hard-fails at boot** with
`AdapterUnavailableError` when the corresponding URL is unset, and any live
backend error at call time raises rather than degrading silently.

## Environment variables

- `SOS_BORDER_PROFILE` — `fixture` (default) | `production`
- `SOS_BORDER_RFID_URL` — live RFID reader base URL (production only)
- `SOS_BORDER_TELEMATICS_URL` — live telematics backend base URL (production only)
- `EVENT_BUS` — `memory` (default) | `kafka` | `fluvio`
- `EVENT_KAFKA_BOOTSTRAP` — required when `EVENT_BUS=kafka`
- `OTEL_EXPORTER_OTLP_ENDPOINT` — optional OpenTelemetry wiring

## Endpoints (prefix `/border/v1`)

- `GET/POST /crossings`, `GET/PATCH/DELETE /crossings/{id}`
- `POST /consignments`, `GET /consignments[?state=]`, `GET /consignments/{id}`
- `POST /consignments/{id}/seal|arrive|clear` (clear appends audit record)
- `GET /clearance-audit` — tenant records + `chain_errors`
- `POST /scans`, `GET /scans[?consignment_id=]`, `POST /scans/pull`
- `POST /telematics/pings`, `GET /telematics/pings`, `POST /telematics/pull`
- `GET /tamper-alerts`
- `POST /levy/quote`, `POST /levy/assess/{consignment_id}`,
  `GET/PUT /levy/policy`
- `/healthz`, `/metrics` (shared `http_*` exposition)

## Run

```bash
pip install -e services/mod-border-transit[dev]
uvicorn app.main:app --app-dir services/mod-border-transit --port 8021
cd services/mod-border-transit && python3 -m pytest
```

## Container

```bash
docker build -t sos-mod-border-transit services/mod-border-transit
docker run -p 8000:8000 sos-mod-border-transit
```
