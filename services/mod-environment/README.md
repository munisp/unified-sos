# mod-environment — Environmental Protection, Carbon Registry & Industrial Emissions (ENV-09)

FastAPI reference module for industrial IoT telemetry compliance, effluent/timber
permits and levies, deforestation satellite surveillance, the state carbon
registry, and EIA workflow. All amounts are integer kobo; every tenant-owned
object carries `tenant_state_id` (`lagos | ogun | osun | benue | nasarawa | taraba`)
and repositories reject cross-tenant access.

## Revenue model (ENV-09)

- **Effluent discharge permits & levies** — permit lifecycle `DRAFT -> ACTIVE ->
  SUSPENDED/EXPIRED` with integer kobo fees (`Permit`).
- **Carbon brokerage** — a fee on every carbon credit transfer, `[DERIVED]`
  conservative default of 3% (`DEFAULT_BROKERAGE_BPS = 300`), overridable per
  state via config; settlement legs post to ledger account `3001` (state CRF/TSA).
- **Timber royalties** — `TIMBER_LOGGING` permit type mirrors the licensed-coupe
  regime enforced by `mod-forestry` stumpage billing.
- **Violation fines** — telemetry violations raise a `ComplianceIncident` with a
  deterministic fine estimate: base fine × per-state multiplier (`[DERIVED]`
  seed; Lagos 2.0×, Ogun 1.5×, others 1.0×), ledger account `3001`.

## KPIs (ENV-09)

- **< 4h alert response** — `raise_deforestation_alert` computes
  `sla_deadline = detected_at + 4h`; `POST .../dispatch` issues a SEC-10
  enforcement ticket reference.
- **Compliance rate** — `GET /environment/v1/facilities/{id}/compliance` returns
  `compliance_rate = (readings - violations) / readings` per facility per tenant.
- **Ecological levy yield** — compliance summary aggregates `total_fines_kobo`
  from incidents plus permit fees in kobo.

## Tech interfaces (ENV-09)

- **Sedona raster / lakehouse** — `DeforestationAlert` is ingested from the
  Sedona/NDVI change-detection job (GeoJSON-like polygon, H3 cell IDs, NDVI
  delta, Sentinel-2/Landsat source). The job itself lives under `geospatial/`
  and `lakehouse/`; this service is the alert consumer `[GAP]`.
- **Kafka/Fluvio event bus** — optional `bus` seam on `EnvironmentService`
  publishes `ng.sos.environment.deforestation_alert_raised` and
  `ng.sos.environment.compliance_violation_raised` (see
  `contracts/asyncapi/platform-events.yaml`).
- **Wazuh** — security monitoring of the service and IoT ingestion edge remains
  an infrastructure adapter seam `[GAP]`; no Wazuh code lives here.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/environment/v1/telemetry` | Ingest reading; returns `COMPLIANT`/`WARNING`/`VIOLATION` (+ incident) |
| GET | `/environment/v1/facilities/{facility_id}/compliance?state_id=` | Facility compliance summary |
| POST | `/environment/v1/permits` | Create effluent/timber permit (DRAFT) |
| POST | `/environment/v1/permits/{permit_id}/activate?state_id=` | DRAFT → ACTIVE |
| POST | `/environment/v1/deforestation-alerts` | Raise NDVI alert (computes 4h SLA) |
| POST | `/environment/v1/deforestation-alerts/{alert_id}/dispatch?state_id=` | Dispatch SEC-10 ticket |
| POST | `/environment/v1/carbon-projects` | Register carbon project |
| POST | `/environment/v1/carbon-credits` | Register credit (unique serial per tenant) |
| POST | `/environment/v1/carbon-credits/{id}/issue?state_id=` | REGISTERED → ISSUED |
| POST | `/environment/v1/carbon-credits/{id}/transfer` | Transfer + brokerage settlement lines |
| POST | `/environment/v1/carbon-credits/{id}/retire?state_id=` | Retire (terminal) |
| POST | `/environment/v1/eias` | Submit EIA application |
| POST | `/environment/v1/eias/{id}/advance` | Advance workflow (`SUBMITTED → SCREENING → PUBLIC_COMMENT → APPROVED/REJECTED`) |
| GET | `/healthz` | Liveness |

## Run tests

```bash
cd services/mod-environment
python -m pytest -q
```

## Provenance

- `[DERIVED]` compliance limits, fine multipliers, base fine, and brokerage fee
  are config-like seed data until state environmental policy packs provide
  authoritative values.
- `[GAP]` live IoT ingestion, Sedona job wiring, Kafka/Fluvio publishers, Wazuh
  agents, and Temporal durable EIA workflows (`temporal_workflow_ref` is a
  placeholder field).
