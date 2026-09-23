"""FastAPI surface for mod-ml-inference (CPU-only ML inference).

Endpoints (all ``/ml/v1/*`` endpoints are tenant-scoped via the required
``X-State-Tenant`` header):

* ``GET  /healthz``
* ``GET  /metrics``             — shared observability + ML series
* ``GET  /ml/v1/models``        — registry listing with model cards
* ``POST /ml/v1/predict/{model}``   — batched prediction (audit-logged)
* ``POST /ml/v1/feedback/{model}``  — ground-truth labels → accuracy/AUC
* ``GET  /ml/v1/drift/{model}``     — PSI drift report
* ``GET  /ml/v1/ab/{model}``        — champion/challenger comparison

Fail-closed seams (mirroring the mod-erp-bridge build-adapter idiom):
``SOS_ML_PROFILE=production`` hard-fails at boot without a populated
artifacts dir (``SOS_ML_ARTIFACTS_DIR``) or ``SOS_MLFLOW_TRACKING_URI``,
and refuses heuristic fallback predictions.
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .ab import ABRouter, build_router, event_payload_hash, sha256_hex, GENESIS_PREV_HASH
from .inference import (
    InferenceEngine,
    InputValidationError,
    build_engine,
)
from .monitoring import DriftMonitor, FeedbackStore
from .registry import (
    AdapterUnavailableError,
    ModelNotFoundError,
    ModelRegistry,
    build_registry,
)

# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    _services_root = Path(__file__).resolve().parents[2]
    if str(_services_root) not in sys.path:
        sys.path.insert(0, str(_services_root))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None

# --- shared event bus (import-guarded; in-memory default) ------------------
try:
    from _shared.eventbus import InMemoryEventBus as _InMemoryEventBus
except ImportError:
    _services_root = Path(__file__).resolve().parents[2]
    if str(_services_root) not in sys.path:
        sys.path.insert(0, str(_services_root))
    try:
        from _shared.eventbus import InMemoryEventBus as _InMemoryEventBus
    except ImportError:
        _InMemoryEventBus = None


class _NullEventBus:
    """Fallback sink when services/_shared is unavailable in the image."""

    def __init__(self) -> None:
        self.events: List = []

    def publish(self, topic: str, payload) -> None:
        self.events.append((topic, payload))


class PredictRequest(BaseModel):
    instances: List[Dict] = Field(..., min_length=1)
    version: Optional[str] = Field(
        None, description="Pin a version; default routes via A/B policy")
    key: Optional[str] = Field(
        None, description="Stable entity key for deterministic A/B assignment")


class CreditScoreRequest(BaseModel):
    applicant_id: str = Field(..., min_length=1)
    features: Optional[Dict] = Field(
        None, description="Explicit feature instance; deterministic default "
                          "derived from applicant_id when omitted")


class FraudScoreRequest(BaseModel):
    entity_id: str = Field(..., min_length=1)
    features: Optional[Dict] = Field(
        None, description="Explicit feature instance; deterministic default "
                          "derived from entity_id when omitted")


class FeedbackRequest(BaseModel):
    prediction: float
    label: float = Field(..., description="Ground-truth label")
    prediction_id: Optional[str] = None


def _tenant(x_state_tenant: Optional[str] = Header(default=None)) -> str:
    if not x_state_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-State-Tenant header is required for ML endpoints")
    return x_state_tenant


class AuditLog:
    """Hash-chained prediction audit log — stores only the inputs hash,
    never raw feature values (no raw PII in the audit chain)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.records: List[Dict] = []

    def append(self, **payload) -> Dict:
        with self._lock:
            prev = self.records[-1]["event_hash"] if self.records else GENESIS_PREV_HASH
            record = dict(payload)
            record["prev_hash"] = prev
            record["event_hash"] = event_payload_hash(record, prev)
            self.records.append(record)
            return record


def get_registry(request: Request) -> ModelRegistry:
    return request.app.state.registry


def get_engine(request: Request) -> InferenceEngine:
    return request.app.state.engine


def create_app(registry: Optional[ModelRegistry] = None,
               engine: Optional[InferenceEngine] = None,
               monitor: Optional[DriftMonitor] = None,
               feedback: Optional[FeedbackStore] = None,
               router: Optional[ABRouter] = None,
               bus=None) -> FastAPI:
    app = FastAPI(
        title="SOS mod-ml-inference — CPU Model Inference",
        version="0.1.0",
        description="Tenant-scoped CPU inference for SOS baseline models "
                    "(fraud_gnn, credit_mlp, luc_avm, crowd_lstm) with drift "
                    "detection, feedback rollups and A/B champion/challenger.",
    )
    # Fail-closed bindings: default fixture profile is deterministic;
    # SOS_ML_PROFILE=production hard-fails here at boot without config.
    app.state.registry = registry or build_registry()
    app.state.bus = bus or (_InMemoryEventBus() if _InMemoryEventBus else _NullEventBus())
    app.state.engine = engine or build_engine(app.state.registry)
    # Warm model weights once at startup (credit_mlp / fraud_gnn / any
    # registered artifact) so the first request doesn't pay load_state_dict.
    app.state.warmed_models = app.state.engine.warmup()
    if monitor is None:
        import os as _os

        monitor = DriftMonitor(
            window_size=int(_os.environ.get("SOS_ML_DRIFT_WINDOW", "200")),
            psi_threshold=float(_os.environ.get("SOS_ML_PSI_THRESHOLD", "0.25")),
            bus=app.state.bus)
    app.state.monitor = monitor
    app.state.feedback = feedback or FeedbackStore()
    app.state.router = router or build_router()
    app.state.audit = AuditLog()

    # --- endpoints -----------------------------------------------------------

    @app.get("/ml/v1/models", tags=["registry"])
    def list_models(tenant: str = Depends(_tenant),
                    reg: ModelRegistry = Depends(get_registry)):
        models = []
        for name in reg.models():
            champion = reg.champion_version(name)
            card = reg.card(name, champion)
            models.append({
                "model_name": name,
                "champion_version": champion,
                "challenger_version": reg.challenger_version(name),
                "versions": reg.versions(name),
                "card": card.model_dump(),
            })
        return {"tenant_state_id": tenant, "models": models}

    @app.post("/ml/v1/predict/{model_name}", tags=["inference"])
    def predict(model_name: str, req: PredictRequest,
                request: Request,
                tenant: str = Depends(_tenant)):
        eng: InferenceEngine = request.app.state.engine
        reg: ModelRegistry = request.app.state.registry
        ab: ABRouter = request.app.state.router
        mon: DriftMonitor = request.app.state.monitor

        variant = "champion"
        version = req.version
        if version is None and model_name in reg.models():
            champion = reg.champion_version(model_name)
            challenger = reg.challenger_version(model_name)
            key = req.key or uuid.uuid4().hex
            variant, version = ab.assign(model_name, key, champion, challenger)
            ab.log_assignment(model_name, key, variant, version, tenant)

        prediction_id = f"pred-{uuid.uuid4().hex[:16]}"
        start = time.perf_counter()
        try:
            result = eng.predict(model_name, req.instances, version=version)
        except ModelNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"model {model_name!r} not found in registry")
        except InputValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc))
        except AdapterUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc))
        elapsed = time.perf_counter() - start

        if model_name in reg.models():
            ab.log_outcome(model_name, variant, result["model_version"],
                           prediction_id, elapsed, ok=True)

        # Drift observations (numeric scalar features only).
        card = None
        if result["model_version"] != "fixture":
            try:
                card = reg.card(model_name, result["model_version"])
            except ModelNotFoundError:  # pragma: no cover - defensive
                card = None
        for instance, score in zip(req.instances, result["predictions"]):
            numeric = {k: float(v) for k, v in instance.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
            mon.record_observation(model_name, numeric, float(score),
                                   result["model_version"], tenant)

        # Audit: hash of inputs only — never raw feature values (PII).
        request.app.state.audit.append(
            event_id=prediction_id,
            tenant_state_id=tenant,
            model_name=model_name,
            model_version=result["model_version"],
            variant=variant,
            inputs_hash=sha256_hex(repr([
                sorted((k, repr(v)) for k, v in i.items())
                for i in req.instances])),
            batch_size=len(req.instances),
            latency_seconds=round(elapsed, 6),
        )
        return {
            "prediction_id": prediction_id,
            "tenant_state_id": tenant,
            "variant": variant,
            **result,
        }

    @app.post("/ml/v1/feedback/{model_name}",
              status_code=status.HTTP_202_ACCEPTED, tags=["feedback"])
    def submit_feedback(model_name: str, req: FeedbackRequest,
                        request: Request,
                        tenant: str = Depends(_tenant)):
        store: FeedbackStore = request.app.state.feedback
        store.record(model_name, req.prediction, req.label,
                     prediction_id=req.prediction_id,
                     tenant_state_id=tenant)
        threshold = None
        reg: ModelRegistry = request.app.state.registry
        if model_name in reg.models():
            threshold = reg.card(model_name).threshold
        return {"accepted": True,
                "rollup": store.rollup(model_name, threshold=threshold)}

    @app.get("/ml/v1/feedback/{model_name}", tags=["feedback"])
    def feedback_rollup(model_name: str, request: Request,
                        tenant: str = Depends(_tenant)):
        store: FeedbackStore = request.app.state.feedback
        threshold = None
        reg: ModelRegistry = request.app.state.registry
        if model_name in reg.models():
            threshold = reg.card(model_name).threshold
        return {"tenant_state_id": tenant,
                **store.rollup(model_name, threshold=threshold)}

    @app.get("/ml/v1/drift/{model_name}", tags=["monitoring"])
    def drift_report(model_name: str, request: Request,
                     tenant: str = Depends(_tenant)):
        mon: DriftMonitor = request.app.state.monitor
        return {"tenant_state_id": tenant, **mon.report(model_name)}

    @app.get("/ml/v1/ab/{model_name}", tags=["ab"])
    def ab_comparison(model_name: str, request: Request,
                      tenant: str = Depends(_tenant)):
        ab: ABRouter = request.app.state.router
        return {"tenant_state_id": tenant, **ab.comparison(model_name)}

    # --- contract scoring endpoints (mod-mortgage / mod-gis-lands seams) ---

    def _default_features(seed: str, schema: Dict) -> Dict:
        """Deterministic default instance derived from an entity id, shaped
        to the model card's feature schema (categorical ids, numerics in
        [0,1), sequences). The same id always yields the same instance, so
        scores stay reproducible when callers omit explicit features."""
        digest = sha256_hex(seed)

        def num(i: int) -> float:
            return int(digest[(i * 8) % 56:(i * 8) % 56 + 8], 16) % 1000 / 1000.0

        if not schema:  # no card registered — flat numeric fixture instance
            return {f"f{i}": num(i) for i in range(4)}
        instance: Dict = {}
        if "features" in schema:  # generic list-format schema
            for i, spec in enumerate(schema["features"]):
                if spec.get("type") == "sequence":
                    instance[spec["name"]] = [num(i), num(i + 1)]
                else:
                    lo = spec.get("min")
                    hi = spec.get("max")
                    value = num(i)
                    if lo is not None and hi is not None:
                        value = float(lo) + value * (float(hi) - float(lo))
                    instance[spec["name"]] = value
            return instance
        if "node_features" in schema:
            return {name: num(i)
                    for i, name in enumerate(schema["node_features"])}
        for name, vocab in (schema.get("categoricals") or {}).items():
            instance[name] = int(digest[:8], 16) % max(1, int(vocab))
        for i, name in enumerate(schema.get("numeric") or []):
            instance[name] = num(i + 1)
        if "density" in schema or "density_history" in schema:
            key = "density" if "density" in schema else "density_history"
            instance[key] = [num(1), num(2), num(3)]
        return instance

    def _score_endpoint(*, model_name: str, entity_id: str,
                        features: Optional[Dict], request: Request,
                        tenant: str, map_score) -> Dict:
        eng: InferenceEngine = request.app.state.engine
        reg: ModelRegistry = request.app.state.registry
        if features is not None:
            instance = dict(features)
        else:
            try:
                schema = reg.card(model_name).feature_schema
            except ModelNotFoundError:
                schema = {}
            instance = _default_features(f"{model_name}:{entity_id}", schema)
        prediction_id = f"pred-{uuid.uuid4().hex[:16]}"
        start = time.perf_counter()
        try:
            result = eng.predict(model_name, [instance])
        except ModelNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"model {model_name!r} not found in registry")
        except InputValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc))
        except AdapterUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc))
        elapsed = time.perf_counter() - start
        raw = float(result["predictions"][0])
        request.app.state.audit.append(
            event_id=prediction_id,
            tenant_state_id=tenant,
            model_name=model_name,
            model_version=result["model_version"],
            variant="scoring",
            inputs_hash=sha256_hex(repr(sorted(
                (k, repr(v)) for k, v in instance.items()))),
            batch_size=1,
            latency_seconds=round(elapsed, 6),
        )
        return {
            "prediction_id": prediction_id,
            "tenant_state_id": tenant,
            "score": map_score(raw),
            "raw_prediction": raw,
            "model_version": result["model_version"],
        }

    @app.post("/ml/v1/credit/score", tags=["scoring"])
    def credit_score(req: CreditScoreRequest, request: Request,
                     tenant: str = Depends(_tenant)):
        """Credit score in [300, 850] via the credit_mlp model.

        Contract seam for mod-mortgage's HttpCreditScorer. The model's
        0-1000 output is linearly mapped onto [300, 850]; fixture-profile
        responses are tagged ``model_version: fixture``.
        """
        return _score_endpoint(
            model_name="credit_mlp", entity_id=req.applicant_id,
            features=req.features, request=request, tenant=tenant,
            map_score=lambda raw: max(300, min(850, int(round(
                300.0 + max(0.0, min(1000.0, raw)) / 1000.0 * 550.0)))))

    @app.post("/ml/v1/fraud/score", tags=["scoring"])
    def fraud_score(req: FraudScoreRequest, request: Request,
                    tenant: str = Depends(_tenant)):
        """Fraud risk score in [0, 100] via the fraud_gnn model.

        Contract seam for mod-gis-lands' HttpTitleRiskScorer. The model's
        probability output is mapped onto [0, 100]; fixture-profile
        responses are tagged ``model_version: fixture``.
        """
        return _score_endpoint(
            model_name="fraud_gnn", entity_id=req.entity_id,
            features=req.features, request=request, tenant=tenant,
            map_score=lambda raw: max(0, min(100, int(round(
                max(0.0, min(1.0, raw)) * 100.0)))))

    @app.get("/healthz")
    def healthz() -> Dict[str, str]:
        return {"status": "ok"}

    # --- /metrics with ML series appended -------------------------------------
    # Registered BEFORE instrument_fastapi so this handler wins route matching.
    registry_obj = None
    if _instrument_fastapi is not None:
        try:
            from _shared.observability import LocalRegistry

            registry_obj = LocalRegistry()
        except ImportError:  # pragma: no cover - defensive
            registry_obj = None

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        from starlette.responses import PlainTextResponse

        reg = registry_obj or getattr(request.app.state,
                                      "observability_registry", None)
        base = reg.render_prometheus("mod-ml-inference") if reg is not None else ""
        eng: InferenceEngine = request.app.state.engine
        mon: DriftMonitor = request.app.state.monitor
        fb: FeedbackStore = request.app.state.feedback
        lines = [
            "# HELP ml_predictions_total Inference batches by model.",
            "# TYPE ml_predictions_total counter",
        ]
        for name, count in sorted(eng.prediction_counts.items()):
            lines.append(f'ml_predictions_total{{model="{name}"}} {count}')
            lat = eng.latency.summary(name)
            lines.append(
                f'ml_predict_latency_p95_seconds{{model="{name}"}} '
                f'{lat["p95_seconds"]}')
            rep = mon.report(name)
            lines.append(
                f'ml_drift_prediction_shift{{model="{name}"}} '
                f'{rep["prediction_shift"]}')
            for feat, value in rep["feature_psi"].items():
                lines.append(
                    f'ml_drift_feature_psi{{model="{name}",feature="{feat}"}} {value}')
            roll = fb.rollup(name)
            if roll["accuracy"] is not None:
                lines.append(
                    f'ml_feedback_accuracy{{model="{name}"}} {roll["accuracy"]}')
            if roll["auc"] is not None:
                lines.append(f'ml_feedback_auc{{model="{name}"}} {roll["auc"]}')
        return PlainTextResponse(base + "\n".join(lines) + "\n")

    # Closed-loop drift remediation (app/remediation.py): subscribes to
    # ng.sos.ml.drift_detected; opt-in so the fixture profile stays inert.
    from .remediation import build_worker as _build_remediation_worker
    app.state.remediation = _build_remediation_worker(bus=app.state.bus)

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-ml-inference", registry=registry_obj)
    return app


app = create_app()
