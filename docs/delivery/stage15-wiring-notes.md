# Stage 15 — Cross-service wiring fixes (notes)

Scope: code-verified broken wiring across compose, mod-ml-inference,
mod-gis-lands, mod-mortgage, mod-mobility-switch and the citizen PWA
payment seam. All changes preserve the repo's fail-closed adapter idiom
(deterministic fixture default in dev; explicit config required in
production).

## 1. mod-geospatial compose wiring (`deploy/docker-compose.yml`)

The service block pointed at the wrong build context, port and upstream:

* **Build context** — the Dockerfile expects the REPO ROOT context (it
  `COPY`s `geospatial/local/`); the block now uses `context: ..` with
  `dockerfile: services/mod-geospatial/Dockerfile`.
* **Ports** — the container serves `:8013` (`EXPOSE 8013`,
  `uvicorn ... --port 8013`); the mapping is now `"8013:8013"` and the
  healthcheck probes `http://localhost:8013/healthz`.
* **Gateway upstream** — `mod-geospatial-gateway`'s
  `GEOSPATIAL_UPSTREAM_URL` is now `http://mod-geospatial:8013`.

## 2. ML scoring contract endpoints (`services/mod-ml-inference`)

mod-mortgage's `HttpCreditScorer` and mod-gis-lands' `HttpTitleRiskScorer`
POSTed to routes that did not exist. Added, both tenant-scoped via the
required `X-State-Tenant` header and hash-chain audit-logged like
`/ml/v1/predict`:

* `POST /ml/v1/credit/score` `{applicant_id, features?}` → `{score:
  int 300-850, raw_prediction, model_version, prediction_id}`. Routes
  through the existing `credit_mlp` inference engine path (artifact when
  registered, deterministic heuristic fallback otherwise); the model's
  0-1000 output is linearly mapped onto [300, 850]. When `features` is
  omitted a deterministic default instance is derived from
  `applicant_id`, shaped to the registered model card's schema.
* `POST /ml/v1/fraud/score` `{entity_id, features?}` → `{score:
  int 0-100, ...}` via the `fraud_gnn` path; probability output ×100.

Fixture-profile responses carry `model_version: "fixture"`, consistent
with the module's existing fixture semantics. Tests:
`services/mod-ml-inference/tests/test_scoring.py` (8 tests: shape,
range, determinism, fixture tagging, tenant enforcement).

## 3. gis-lands phantom adapter defaults (`services/mod-gis-lands/lands_app`)

* `risk.py` — `DEFAULT_RISK_URL` was the **host** port (`:8021`); fixed
  to the in-cluster target `http://mod-ml-inference:8000/ml/v1/fraud/score`.
* `legal_adapters.py` — `DEFAULT_DOCS_URL` had the wrong port AND path;
  now `http://mod-land-docs:8000` with the real verify transition
  (`POST /api/v1/states/{state_id}/land-docs/documents/{id}/verify`).
  `HttpLandDocsAdapter` sends the `VerifyRequest` body, treats 404 as
  not-found, and on 409 (transition not allowed, e.g. already VERIFIED)
  falls back to `GET .../documents/{id}` so idempotent re-verifies are
  not misread as outages; still fail-closed on any other failure.
* **Deleted phantom constants** `DEFAULT_TAX_URL`
  (`mod-revenue-tax:8023`), `DEFAULT_ANCHOR_URL`
  (`mod-anchor-notary:8031`) and `DEFAULT_LEDGER_URL`
  (`mod-finance-ledger:8024`) — none of those services exist.

### External-system seams (documentation decision)

Anchor/notary, tax clearance and the compensation ledger are
**external-system seams**: no in-cluster implementation ships with the
platform. The HTTP adapters (`HttpAnchorAdapter`,
`HttpTaxClearanceAdapter`, `HttpCompensationLedger`) now require an
explicit env URL (`SOS_LANDS_ANCHOR_URL` / `SOS_LANDS_TAX_URL` /
`SOS_LANDS_LEDGER_URL`) and raise `AdapterUnavailableError` when
constructed without one — fail-closed in production, with the
deterministic fixture adapters remaining the dev/test default
(`SOS_LANDS_PROFILE=production` already hard-required the env at boot).
This note stands in for README coverage: the stage scope restricted
gis-lands edits to `lands_app/{risk,legal_adapters,anchoring,revocation}.py`.

## 4. mod-mortgage adapter defaults (`mortgage_app/adapters.py`)

* `HttpLandRegistry.DEFAULT_URL` and the new
  `HttpTitleTransfer.DEFAULT_URL` are `http://mod-gis-lands:8000`
  (in-cluster compose port; `:8003` was phantom) and are now actually
  used as the documented fallback when `SOS_MORTGAGE_LANDS_URL` is
  unset — the env var overrides the default.
* `HttpCreditScorer.DEFAULT_URL` was also on the host port; fixed to
  `http://mod-ml-inference:8000/ml/v1/credit/score` and given the same
  fallback semantics.
* The two existing tests that asserted fail-closed-without-URL were
  updated to assert the documented default-URL fallback instead
  (behaviour change per stage directive).

## 5. docker-compose missing services

Added blocks following the exact existing pattern (build context +
`ghcr.io/munisp/<name>:dev` image, `TENANT_STATE_ID`, fixture envs,
python-urllib `:8000/healthz` healthcheck). Host port assignments
(containers all serve :8000; 8000-8023, 8443, 5000 were taken):

| Host port | Service |
|---|---|
| 8024 | control-plane |
| 8025 | mod-identity |
| 8026 | mod-mobility-switch |
| 8027 | mod-police-cad |
| 8028 | mod-market |
| 8029 | mod-mining |
| 8030 | mod-forestry |
| 8031 | mod-education |
| 8032 | mod-health |
| 8033 | mod-gis-luc |
| 8034 | mod-agri-waybill |
| 8035 | mod-ppp-investment |
| 8036 | mod-transport-wim |

(mod-kyc-kyb was already present at 8012.)

* **apisix** — `apache/apisix:3.9.0` (override via `APISIX_IMAGE`),
  host port 9080, `deploy/tuning/apisix.yaml` mounted as
  `conf/config.yaml`. The shared tuning config is etcd-driven; a fully
  standalone local gateway (no etcd) needs an image baked with the
  standalone `deployment` block + `apisix-routes.generated.yaml`
  (`deploy/tuning/generate_apisix_routes.py`) — hence the documented
  `APISIX_IMAGE` override.
* **citizen-pwa** — build context `apps/citizen-pwa`, image
  `ghcr.io/munisp/citizen-pwa:dev` (override via `CITIZEN_PWA_IMAGE`),
  host port **3001** → container 3000 (host 3000 is already tigerbeetle;
  the Caddy edge targets `citizen-pwa:3000` over the internal network).
  Note: `apps/citizen-pwa/Dockerfile` does not exist yet — it ships with
  the PWA workstream; until then use `CITIZEN_PWA_IMAGE` with a prebuilt
  image.

Pre-existing (not touched): host port 8080 is published by both
`mod-rev-core` and `caddy` (`8080:80`) — flagged for a follow-up.

## 6. Citizen-PWA payment quotes (`services/mod-mobility-switch`)

`apps/citizen-pwa/src/lib/api.ts` called `/payments/v1/quotes` and
`/payments/v1/quotes/{id}/confirm`, which nothing served. Implemented in
`app/main.py`, tenant-scoped via the required `X-State-Tenant` header:

* `POST /payments/v1/quotes` `{bill_reference, amount_kobo, payer}`
  (`ticket_ref` accepted as a PWA alias) → `{quote_id, amount_kobo,
  fees_kobo, expires_at, status}`. Fees come from the FSPIOP adapter
  seam when wired; otherwise the deterministic fixture fee (1% + ₦10.00,
  the same formula as `FixtureFspiopAdapter.quote`). The quote id is a
  deterministic hash of (tenant, reference, amount, payer), so identical
  inputs replay the same pending quote.
* `POST /payments/v1/quotes/{quote_id}/confirm` executes settlement
  through the existing FSPIOP prepare → fulfil settle path (fixture
  in-memory when no scheme adapter is wired). Idempotent on the
  `Idempotency-Key` header (replay with the same key returns the settled
  record; a different key on a settled quote → 409). Expired quotes →
  410; unknown quotes → 404.

Tests: `services/mod-mobility-switch/tests/test_payments.py` (13 tests).

## Validation

* `python3 -c 'import yaml; yaml.safe_load(open("deploy/docker-compose.yml"))'`
  — parses; no new host-port collisions (see port table above).
* pytest (venv with pytest/fastapi/httpx/pydantic/prometheus-client;
  torch present on the host so the full ml-inference suite ran):
  * mod-ml-inference: 60 passed
  * mod-mobility-switch: 44 passed
  * mod-mortgage: 98 passed
  * mod-gis-lands: 145 passed
