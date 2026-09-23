"""Performance-oriented tests: model warmup at startup (weights loaded once),
champion/challenger pointer caching (no per-request file reads), and the
batched scoring path. API contracts for /ml/v1/credit/score and
/ml/v1/fraud/score are covered by test_scoring.py (unchanged)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from app.inference import InferenceEngine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.mock_models import MOCK_ARCHITECTURES  # noqa: E402
from app.registry import FeatureSchema, ModelRegistry  # noqa: E402

TENANT = {"X-State-Tenant": "lagos"}

SCHEMA = {"features": [
    {"name": "in_degree", "type": "float"},
    {"name": "out_degree", "type": "float"},
    {"name": "tx_amount", "type": "float"},
]}
INSTANCE = {"in_degree": 3.0, "out_degree": 1.0, "tx_amount": 500.0}


def _write_model(root: Path, name: str, version: str) -> None:
    version_dir = root / name / version
    version_dir.mkdir(parents=True, exist_ok=True)
    model = MOCK_ARCHITECTURES[name].from_schema(FeatureSchema.model_validate(SCHEMA))
    torch.save(model.state_dict(), version_dir / "weights.pt")
    (version_dir / "model_card.json").write_text(json.dumps({
        "model_name": name, "version": version,
        "feature_schema": SCHEMA, "threshold": 0.5,
    }))
    (root / name / "champion").write_text(version)


def _registry(tmp_path: Path) -> ModelRegistry:
    _write_model(tmp_path, "fraud_gnn", "v1")
    _write_model(tmp_path, "credit_mlp", "v1")
    return ModelRegistry(tmp_path)


def test_warmup_invoked_once_at_startup(tmp_path, monkeypatch):
    calls: list[list] = []
    original = InferenceEngine.warmup

    def spy(self, model_names=None):
        calls.append(list(model_names or []))
        return original(self, model_names)

    monkeypatch.setattr(InferenceEngine, "warmup", spy)
    app = create_app(registry=_registry(tmp_path))
    assert len(calls) == 1  # exactly once, at app construction
    assert set(app.state.warmed_models) == {"fraud_gnn", "credit_mlp"}
    # weights already cached — no lazy load on first request
    assert ("fraud_gnn", "v1") in app.state.engine._cache
    assert ("credit_mlp", "v1") in app.state.engine._cache


def test_predict_after_warmup_does_not_reload_weights(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    engine = InferenceEngine(registry, profile="fixture", threads=1)
    app = create_app(registry=registry, engine=engine)
    loads: list[tuple] = []
    original_torch_load = torch.load

    def spy_torch_load(*args, **kwargs):
        loads.append(args[0] if args else kwargs.get("f"))
        return original_torch_load(*args, **kwargs)

    monkeypatch.setattr(torch, "load", spy_torch_load)
    client = TestClient(app)
    for _ in range(3):
        resp = client.post("/ml/v1/predict/fraud_gnn",
                           json={"instances": [INSTANCE]}, headers=TENANT)
        assert resp.status_code == 200, resp.text
    assert loads == []  # warmup already loaded weights; no per-request reads


def test_warmup_is_idempotent(tmp_path):
    engine = InferenceEngine(_registry(tmp_path), profile="fixture", threads=1)
    first = engine.warmup()
    cache_size = len(engine._cache)
    second = engine.warmup()
    assert first == second and len(engine._cache) == cache_size


def test_champion_pointer_cached_across_requests(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    assert registry.champion_version("fraud_gnn") == "v1"
    reads: list[Path] = []
    original_read_text = Path.read_text

    def spy_read_text(self, *args, **kwargs):
        reads.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    for _ in range(5):
        assert registry.champion_version("fraud_gnn") == "v1"
    pointer_reads = [p for p in reads if p.name in ("champion", "challenger")]
    assert pointer_reads == []  # mtime-gated cache serves repeat lookups
