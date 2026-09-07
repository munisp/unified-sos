# mod-ml-inference

CPU-only ML inference microservice for the Nigerian State Operating System.
FastAPI + pydantic v2, torch CPU, tenant-scoped, with drift detection,
feedback rollups and A/B champion/challenger routing.

## Model inventory

| Model        | Task                                            | Output contract              |
|--------------|-------------------------------------------------|------------------------------|
| `fraud_gnn`  | Graph features → per-node fraud probability     | probability 0–1 (+ label at card threshold) |
| `credit_mlp` | Tabular → informal-sector creditworthiness      | score 0–1000                 |
| `luc_avm`    | Property features → Land Use Charge valuation   | valuation in NGN             |
| `crowd_lstm` | Density window → next-interval crowd density    | density 0–1                  |

Architectures are imported from the training stack's `ml` package
(`ml/models/*.py`, class names `FraudGNN` / `CreditMLP` / `LUCAVM` /
`CrowdLSTM`); the repo root is added to `sys.path` with the same
import-guard idiom as `services/_shared/observability.py`. Per-model
adapters in `app/inference.py` featurize request JSON into CPU tensors and
post-process raw outputs into the contract units (e.g. `luc_avm` inverts
`log1p` → NGN; `credit_mlp` maps the logit to 0–1000).

If `ml/` is unavailable at runtime: the **fixture** profile serves a
deterministic heuristic fallback tagged `model_version: fixture`;
**production** fails closed with a clear `AdapterUnavailableError`
(HTTP 503) — heuristic output is never served in production.

## Artifacts, registry & promotion flow

Artifacts are mounted at `SOS_ML_ARTIFACTS_DIR` (default `ml/artifacts`):

```
<artifacts>/<model>/<version>/weights.pt        # torch CPU state_dict
<artifacts>/<model>/<version>/model_card.json   # see contract below
<artifacts>/<model>/champion                    # pointer file: version string
<artifacts>/<model>/challenger                  # optional pointer file
```

`model_card.json` contract: `{model_name, version, metrics,
feature_schema, threshold, trained_at, dataset_hash}`. Cards whose
`model_name` does not match the directory are rejected; versions missing
`weights.pt` or the card are never served.

**Promotion** is a pointer flip: write the target version into the
`champion` file. Absent a pointer, the highest version wins. Write a
version into `challenger` and set `SOS_ML_CHALLENGER_PCT` to A/B it
against the champion.

**MLflow seam**: set `SOS_MLFLOW_TRACKING_URI` to bind a tracking server.
Fail-closed: with the URI set but the optional `mlflow` package missing,
production boot raises rather than running untracked.

## CPU inference guidance

* CPU-only: `torch.set_num_threads(SOS_ML_THREADS)` (default 1), CPU
  tensors, `torch.no_grad()` + `eval()`; models load lazily and are cached
  per `(model, version)`.
* Batches up to 1024 instances per request; per-model latency (mean/p50/
  p95) is tracked and exported on `/metrics`.
* Expectation on a single vCPU: single-row `credit_mlp`/`luc_avm`/
  `crowd_lstm` predictions are low-single-digit ms; `fraud_gnn` batches of
  a few hundred nodes stay in the tens of ms. Keep `SOS_ML_THREADS` at the
  container CPU limit; batch small requests instead of raising threads.

## Drift detection & feedback

* Per-feature **PSI** (population stability index) of a rolling window vs
  the baseline window (or `metrics.drift_baseline` histograms in the
  card), plus prediction-distribution shift vs
  `metrics.baseline_prediction_mean`. PSI ≥ `SOS_ML_PSI_THRESHOLD`
  (default 0.25) flags drift; alerts are published to
  `ng.sos.ml.drift_detected` via the shared event bus.
* `POST /ml/v1/feedback/{model}` records ground-truth labels; rolling
  accuracy (threshold classification for binary labels) and Mann-Whitney
  AUC are exposed on `GET /ml/v1/feedback/{model}` and `/metrics`.

## A/B champion/challenger

`SOS_ML_CHALLENGER_PCT` (default 0) percent of requests — bucketed
deterministically by SHA-256 of `(model, key)` — are routed to the
challenger. Assignments and outcomes are appended to a hash-chained log
(`_shared.hashchain`); only the key hash is stored. Compare variants on
`GET /ml/v1/ab/{model}`.

## Endpoints

| Method | Path                          | Notes                                   |
|--------|-------------------------------|-----------------------------------------|
| GET    | `/healthz`                    | liveness                                |
| GET    | `/metrics`                    | shared observability + ML series        |
| GET    | `/ml/v1/models`               | registry listing with model cards       |
| POST   | `/ml/v1/predict/{model}`      | batched prediction (`instances`, optional `version`, `key`) |
| POST   | `/ml/v1/feedback/{model}`     | ground-truth label → accuracy/AUC       |
| GET    | `/ml/v1/feedback/{model}`     | current rollup                          |
| GET    | `/ml/v1/drift/{model}`        | PSI + prediction-shift report           |
| GET    | `/ml/v1/ab/{model}`           | champion/challenger comparison          |

All `/ml/v1/*` endpoints require the `X-State-Tenant` header. Predictions
are audit-logged on a hash chain containing only the **inputs hash** — no
raw feature values (no raw PII).

## Environment variables

| Variable                     | Default        | Meaning                                    |
|------------------------------|----------------|--------------------------------------------|
| `SOS_ML_PROFILE`             | `fixture`      | `fixture`/`local`/`test` or `production`   |
| `SOS_ML_ARTIFACTS_DIR`       | `ml/artifacts` | mounted artifacts volume                   |
| `SOS_MLFLOW_TRACKING_URI`    | unset          | MLflow tracking seam (fail-closed)         |
| `SOS_ML_THREADS`             | `1`            | torch CPU threads                          |
| `SOS_ML_CHALLENGER_PCT`      | `0`            | % of keyed requests routed to challenger   |
| `SOS_ML_DRIFT_WINDOW`        | `200`          | rolling drift window size                  |
| `SOS_ML_PSI_THRESHOLD`       | `0.25`         | PSI / prediction-shift alert threshold     |

`SOS_ML_PROFILE=production` hard-fails at boot without a populated
artifacts dir or `SOS_MLFLOW_TRACKING_URI` (mod-erp-bridge fail-closed
adapter idiom).

## Run & test

```bash
cd services/mod-ml-inference
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install fastapi pydantic uvicorn pytest httpx
python -m pytest tests -v          # 46 tests
uvicorn app.main:app --port 8000
```

Docker (from this directory):

```bash
docker build -t sos-mod-ml-inference .
docker run -p 8000:8000 -v $PWD/../../ml/artifacts:/srv/artifacts sos-mod-ml-inference
```
