# Stage 7.B — E2E integration harness

End-to-end journey tests that drive several SOS services together. Two tiers:

## In-process tier (default, always-running)

```sh
make test-e2e          # or: python -m pytest tests/e2e -q
```

No Docker, no network. `conftest.py` loads each service's `create_app`
under a unique synthetic package (`s7e2e_<alias>` rooted at the service
directory) and talks to the apps over synchronous httpx ASGI transports.
Compared to the contracts generator's `sys.modules` eviction, aliasing
keeps multiple same-named `app` packages alive in one pytest process
without breaking request-time relative imports.

Journeys:

| Suite | Path exercised |
| --- | --- |
| `test_journey_citizen_revenue.py` | portal service request + USSD session walk → KYC case approved (fixture adapters) → FSPIOP fixture quote/prepare/fulfil → deterministic ledger split posted to the in-memory TigerBeetle-fake harness → control-plane audit chain archived → `sosctl audit verify-chain` logic (`sosctl.audit.verify_tenant_chain`) verifies intact → transparency feed redaction (no PII) |
| `test_journey_tenant_provisioning.py` | control-plane `create_tenant` (local operators) → per-step audit events in the fixed operator order → injected step failure triggers reverse-order rollback → idempotent resume converges the same tenant → cross-tenant read isolation negative check (403) via mod-kyc-kyb |
| `test_journey_offline_pos.py` | edge-daemon SQLite outbox signs Ed25519 stallage tickets offline → `SyncEngine` pushes through an in-process gateway shim into mod-market ingestion → replay returns server-side duplicates (dedupe on `(device_id, sequence)`, no double ticketing) → reconciliation against the in-memory ledger shows zero discrepancy |

### The ledger harness

`InMemoryLedgerHarness` + `compute_split` in `conftest.py` are a Python
reference port of the Go `ledger/splits` in-memory TigerBeetle fake and
`ComputeSplit` (the semantics mod-rev-core runs with
`REV_CORE_LEDGER=memory`): deterministic kobo rounding with the pool
remainder on the last INSTANT leg, idempotent transfer IDs (replay →
exists error), no-overdraft with in-batch staged balances. The Go
toolchain is not available in the dev sandbox; the live tier points the
same journeys at the real Go service.

## Live tier (CI / deployment-run; no Docker in the sandbox)

```sh
docker compose -f tests/e2e/docker-compose.integration.yaml up -d --wait
E2E_STACK=live \
E2E_PORTAL_BASE_URL=http://localhost:8211 \
E2E_KYC_BASE_URL=http://localhost:8212 \
E2E_MARKET_BASE_URL=http://localhost:8213 \
E2E_TRANSPARENCY_BASE_URL=http://localhost:8215 \
E2E_CONTROL_PLANE_BASE_URL=http://localhost:8210 \
python -m pytest tests/e2e -q
```

The compose profile wires the journey services against real
TigerBeetle/Postgres/Redpanda/OpenSearch/MinIO/Keycloak images, with env
set per each adapter's fail-closed contract (e.g. `AUDIT_ARCHIVE=opensearch`
requires `OPENSEARCH_URL`; the portal telco webhooks require
`CITIZEN_PORTAL_TELCO_SECRET`).

With `E2E_STACK=live` the conftest client fixtures return plain
`httpx.Client` against those base URLs (fail-closed if a URL is unset), so
the same journey tests exercise the live stack. Legs that are inherently
in-process (the Python ledger harness, the control-plane metadata-store
handle, the edge outbox) still run locally — the wire protocols and
verification logic are identical. Tests marked `e2e_live`
(`test_live_stack_smoke.py`) are skip-gated unless `E2E_STACK=live`.

Markers: every test in this directory is marked `e2e`; live-only tests are
additionally marked `e2e_live`.
