# SOS ML Training Stack

End-to-end ML/DL/GNN training for the Nigerian State Operating System: synthetic
data generators, CPU PyTorch models, real training loops, a champion/challenger
registry, and continuous-training promotion. Ships committed baseline weights in
`ml/artifacts/` so the inference service has real state_dicts to serve.

## Honest status

The shipped baselines are **trained on synthetic data** (`ml/data/synthetic.py`),
not production revenue data. They exist to (a) prove the full train → register →
serve loop end-to-end and (b) give the inference service well-formed weights with
documented feature schemas and thresholds. The honest path to production weights
is the continuous trainer: point the lakehouse adapter at real gold-layer
extracts (`SOS_ML_LAKEHOUSE_URI`) and challengers are promoted only when they
beat the serving champion by a margin on validation metrics.

## Layout

```
ml/
  data/synthetic.py          # deterministic Nigerian-context generators (fraud
                             # transactions, credit, LUC properties, crowd)
  data/lakehouse_extract.py  # fail-closed lakehouse adapter + schema validation
  models/                    # fraud_gnn, credit_mlp, luc_avm, crowd_lstm (CPU)
  training/train.py          # training loops + CLI + artifact writer
  training/continuous.py     # watch extracts, retrain, champion/challenger
  registry.py                # file registry default, MLflow seam (fail-closed)
  artifacts/<model>/<ver>/   # weights.pt + model_card.json (inference contract)
  tests/                     # pytest: determinism, training, contract, registry
```

## Quick start

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install pandas numpy pytest

python -m ml.training.train --model all --epochs 10     # train baselines
python -m pytest ml/tests -q                            # run tests
```

CLI: `--model fraud_gnn|credit_mlp|luc_avm|crowd_lstm|all --epochs N
--data-dir DIR --artifacts-dir DIR --ray` (ray tasks used only if installed;
default single-process CPU).

## Models and baselines (synthetic data, CPU)

| model      | task                                    | baseline metric      |
|------------|-----------------------------------------|----------------------|
| fraud_gnn  | GraphSAGE node classifier, fraud rings  | test AUC ≈ 0.97      |
| credit_mlp | informal-sector creditworthiness        | test AUC ≈ 0.76      |
| luc_avm    | property/LUC valuation (log NGN)        | test R² ≈ 0.65 (≥0.5 target) |
| crowd_lstm | next-hour crowd density                 | test MSE ≈ 0.005     |

All artifacts are `< 1 MB` CPU `state_dict`s; inference is CPU-first.

## Lakehouse feed

`ml/data/lakehouse_extract.py` mirrors `services/lakehouse` (medallion
bronze→silver→gold). Default fixture adapter regenerates the synthetic
datasets; set `SOS_MLFLOW_TRACKING_URI`-style seam `SOS_ML_LAKEHOUSE_URI` to a
directory with gold extracts (`gold/<dataset>.parquet|csv`) for live data.
Unknown/invalid URIs raise — the adapter never silently reads unvalidated data.
Splits are time-based where a timestamp exists (no leakage).

## Continuous training

```bash
python -m ml.training.continuous --model credit_mlp --watch-dir /path/to/extracts \
    --margin 0.005 --poll-interval 300
```

New extracts trigger a retrain; the challenger is registered and promoted only
if its primary metric beats the champion by `--margin` (higher-is-better for
AUC/R², lower for MSE). Rollback: `ModelRegistry.rollback(name)` flips the
production pointer to the previous version.

## Registry & MLflow seam

Default registry is local files under `ml/registry/<name>/v<N>/{weights.pt,
model_card.json}` + a `current` pointer. Setting `SOS_MLFLOW_TRACKING_URI`
mirrors registrations into MLflow; if the URI is set but mlflow is not
installed, registry construction **raises** (fail-closed — production must not
run untracked).

## Artifact contract (with the inference service)

`ml/artifacts/<model_name>/<version>/weights.pt` — `torch.save` CPU state_dict
— plus `model_card.json` with `model_name, version, metrics{...},
feature_schema{...}, threshold, trained_at, dataset_hash` (+ `code_hash`,
framework versions). Committed `v1` artifacts are the platform's shipped
baselines.
