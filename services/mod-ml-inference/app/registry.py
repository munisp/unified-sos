"""Model registry for mod-ml-inference.

Loads versioned baseline-model artifacts from a mounted volume
(``SOS_ML_ARTIFACTS_DIR``, default ``ml/artifacts``) laid out as::

    <artifacts_dir>/<model_name>/<version>/weights.pt      # torch CPU state_dict
    <artifacts_dir>/<model_name>/<version>/model_card.json # model card
    <artifacts_dir>/<model_name>/champion                  # optional pointer file
    <artifacts_dir>/<model_name>/challenger                # optional pointer file

The model card contract (training stack, built in parallel)::

    {"model_name": str, "version": str, "metrics": {...},
     "feature_schema": {"features": [{"name": ..., "type": ...}]},
     "threshold": float|null, "trained_at": str, "dataset_hash": str}

Selection idiom mirrors the mod-erp-bridge ``build_adapter`` fail-closed
adapter idiom: the default ``fixture``/unset profile tolerates a missing or
empty artifacts dir (the inference engine then serves its deterministic
heuristic fallback tagged ``model_version: fixture``), while
``SOS_ML_PROFILE=production`` hard-fails AT BOOT with
:class:`AdapterUnavailableError` unless either a populated artifacts dir or
``SOS_MLFLOW_TRACKING_URI`` is configured.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / "ml" / "artifacts"

FIXTURE_PROFILES = ("fixture", "local", "test")
PRODUCTION_PROFILES = ("production", "live")




class AdapterUnavailableError(RuntimeError):
    """Raised when a production profile is selected without configuration."""


class ModelNotFoundError(KeyError):
    """Raised when a model or version is not present in the registry."""


class FeatureSpec(BaseModel):
    name: str
    type: str = Field("float", description="float | int | sequence")
    min: Optional[float] = None
    max: Optional[float] = None


class FeatureSchema(BaseModel):
    features: List[FeatureSpec] = Field(default_factory=list)


class ModelCard(BaseModel):
    """Contract with the training stack (ml/training → ml/artifacts).

    ``feature_schema`` is free-form: the training stack writes either the
    ``ml.models.<model>.feature_schema()`` dict (``node_features`` /
    ``categoricals`` + ``numeric`` / sequence spec) or the generic
    ``{"features": [{"name", "type", ...}]}`` list format used by the local
    mock architectures.
    """

    model_name: str
    version: str
    metrics: Dict = Field(default_factory=dict)
    feature_schema: Dict = Field(default_factory=dict)
    threshold: Optional[float] = None
    trained_at: Optional[str] = None
    dataset_hash: Optional[str] = None


def _version_sort_key(version: str):
    parts = re.split(r"[.\-_]", version)
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


class ModelRegistry:
    """Filesystem-backed model registry with champion/challenger pointers."""

    def __init__(self, artifacts_dir: Path | str,
                 mlflow_tracking_uri: Optional[str] = None) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.mlflow_tracking_uri = (mlflow_tracking_uri or "").strip() or None
        self._cards: Dict[tuple, ModelCard] = {}
        self._scan()

    # -- loading ---------------------------------------------------------

    def _scan(self) -> None:
        self._cards.clear()
        if not self.artifacts_dir.is_dir():
            return
        for model_dir in sorted(self.artifacts_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            for version_dir in sorted(model_dir.iterdir()):
                if not version_dir.is_dir():
                    continue
                card_path = version_dir / "model_card.json"
                weights_path = version_dir / "weights.pt"
                if not (card_path.is_file() and weights_path.is_file()):
                    continue  # incomplete artifact: never serve it
                card = ModelCard.model_validate(
                    json.loads(card_path.read_text(encoding="utf-8")))
                if card.model_name != model_dir.name:
                    raise ValueError(
                        f"model card name {card.model_name!r} does not match "
                        f"artifact directory {model_dir.name!r}")
                self._cards[(model_dir.name, version_dir.name)] = card

    # -- queries -----------------------------------------------------------

    def models(self) -> List[str]:
        return sorted({name for name, _ in self._cards})

    def versions(self, model_name: str) -> List[str]:
        versions = [v for (n, v) in self._cards if n == model_name]
        return sorted(versions, key=_version_sort_key)

    def champion_version(self, model_name: str) -> str:
        pointer = self.artifacts_dir / model_name / "champion"
        if pointer.is_file():
            version = pointer.read_text(encoding="utf-8").strip()
            if (model_name, version) in self._cards:
                return version
        versions = self.versions(model_name)
        if not versions:
            raise ModelNotFoundError(model_name)
        return versions[-1]

    def challenger_version(self, model_name: str) -> Optional[str]:
        pointer = self.artifacts_dir / model_name / "challenger"
        if pointer.is_file():
            version = pointer.read_text(encoding="utf-8").strip()
            if (model_name, version) in self._cards:
                return version
        return None

    def card(self, model_name: str, version: Optional[str] = None) -> ModelCard:
        version = version or self.champion_version(model_name)
        try:
            return self._cards[(model_name, version)]
        except KeyError:
            raise ModelNotFoundError(f"{model_name}@{version}") from None

    def weights_path(self, model_name: str,
                     version: Optional[str] = None) -> Path:
        version = version or self.champion_version(model_name)
        path = self.artifacts_dir / model_name / version / "weights.pt"
        if not path.is_file():
            raise ModelNotFoundError(f"{model_name}@{version}: weights.pt missing")
        return path

    # -- MLflow seam --------------------------------------------------------

    def mlflow_available(self) -> bool:
        """True when a tracking URI is configured AND the optional mlflow
        client package is importable. Import-guarded; never raises."""
        if not self.mlflow_tracking_uri:
            return False
        try:  # optional dependency
            import mlflow  # noqa: F401
        except ImportError:
            return False
        return True


def build_registry(env: Optional[Dict[str, str]] = None) -> ModelRegistry:
    """Select the registry from ``SOS_ML_PROFILE`` (fail-closed idiom).

    Default ``fixture``/unset tolerates missing artifacts (heuristic
    fallback in the engine). ``production`` fails closed AT BOOT unless a
    populated artifacts dir or ``SOS_MLFLOW_TRACKING_URI`` is configured.
    """
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_ML_PROFILE", "fixture")
    artifacts_dir = Path(env.get("SOS_ML_ARTIFACTS_DIR") or DEFAULT_ARTIFACTS_DIR)
    tracking_uri = (env.get("SOS_MLFLOW_TRACKING_URI") or "").strip() or None

    if profile in FIXTURE_PROFILES:
        return ModelRegistry(artifacts_dir, tracking_uri)
    if profile in PRODUCTION_PROFILES:
        registry = ModelRegistry(artifacts_dir, tracking_uri)
        if not registry.models() and not tracking_uri:
            raise AdapterUnavailableError(
                "SOS_ML_PROFILE=production requires a populated artifacts dir "
                f"({artifacts_dir} missing/empty) or SOS_MLFLOW_TRACKING_URI"
            )
        if tracking_uri and not registry.mlflow_available():
            raise AdapterUnavailableError(
                "SOS_MLFLOW_TRACKING_URI is set but the optional 'mlflow' "
                "package is not installed (fail-closed MLflow seam)"
            )
        return registry
    raise AdapterUnavailableError(
        f"unknown SOS_ML_PROFILE {profile!r}; expected fixture|production"
    )
