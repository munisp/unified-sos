"""Model registry client.

Default: local file registry under ``ml/registry/`` — versions as
``<model_name>/v<N>/`` containing ``weights.pt`` + ``model_card.json`` and a
``current`` pointer file per model. Promotion is atomic-by-construction
(pointer flip) and rollback is pointer restore.

MLflow seam: set ``SOS_MLFLOW_TRACKING_URI`` to mirror registrations into a
tracking server. Fail-closed: if the URI is set but mlflow is not
importable, registry writes raise rather than silently skipping the mirror
(production deployments must not drift from the tracked state).
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_DIR = Path(__file__).parent / "registry"
MLFLOW_URI_ENV = "SOS_MLFLOW_TRACKING_URI"


class RegistryError(RuntimeError):
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelRegistry:
    """File-backed champion/challenger model registry."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else REGISTRY_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self.mlflow_uri = os.environ.get(MLFLOW_URI_ENV)
        self._mlflow = None
        if self.mlflow_uri:
            try:
                import mlflow  # type: ignore

                mlflow.set_tracking_uri(self.mlflow_uri)
                self._mlflow = mlflow
            except ImportError as e:
                raise RegistryError(
                    f"{MLFLOW_URI_ENV} is set but mlflow is not installed; "
                    "refusing to run untracked (fail-closed)") from e

    # ----------------------------------------------------------- versions --
    def _model_dir(self, name: str) -> Path:
        return self.root / name

    def versions(self, name: str) -> list[str]:
        d = self._model_dir(name)
        if not d.exists():
            return []
        return sorted((p.name for p in d.iterdir()
                       if p.is_dir() and p.name.startswith("v")),
                      key=lambda v: int(v[1:]))

    def next_version(self, name: str) -> str:
        vs = self.versions(name)
        return f"v{(int(vs[-1][1:]) + 1) if vs else 1}"

    def current_version(self, name: str) -> str | None:
        ptr = self._model_dir(name) / "current"
        return ptr.read_text().strip() if ptr.exists() else None

    # ----------------------------------------------------------- register --
    def register(self, name: str, weights_path: Path | str, model_card: dict,
                 promote: bool = True) -> str:
        """Register a new version; returns the version id."""
        version = model_card.get("version") or self.next_version(name)
        model_card = dict(model_card, model_name=name, version=version,
                          registered_at=_utcnow())
        dest = self._model_dir(name) / version
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(weights_path, dest / "weights.pt")
        (dest / "model_card.json").write_text(json.dumps(model_card, indent=2))
        if promote:
            self.promote(name, version)
        self._mirror_mlflow(name, version, model_card, dest / "weights.pt")
        return version

    def _mirror_mlflow(self, name: str, version: str, card: dict, weights: Path) -> None:
        if not self._mlflow:
            return
        with self._mlflow.start_run(run_name=f"{name}-{version}"):
            self._mlflow.log_params({"model_name": name, "version": version})
            for k, v in (card.get("metrics") or {}).items():
                if isinstance(v, (int, float)):
                    self._mlflow.log_metric(k, v)
            self._mlflow.log_artifact(str(weights))

    # ------------------------------------------------------------ promote --
    def promote(self, name: str, version: str) -> None:
        if version not in self.versions(name):
            raise RegistryError(f"{name}/{version} is not registered")
        (self._model_dir(name) / "current").write_text(version)

    def rollback(self, name: str) -> str:
        """Point production back at the previous registered version."""
        vs = self.versions(name)
        cur = self.current_version(name)
        if cur is None or cur not in vs:
            raise RegistryError(f"no current version for {name!r}")
        idx = vs.index(cur)
        if idx == 0:
            raise RegistryError(f"{name!r} has no earlier version to roll back to")
        prev = vs[idx - 1]
        self.promote(name, prev)
        return prev

    # --------------------------------------------------------------- read --
    def load_card(self, name: str, version: str | None = None) -> dict:
        version = version or self.current_version(name)
        if version is None:
            raise RegistryError(f"no current version for {name!r}")
        return json.loads((self._model_dir(name) / version / "model_card.json").read_text())

    def weights_path(self, name: str, version: str | None = None) -> Path:
        version = version or self.current_version(name)
        if version is None:
            raise RegistryError(f"no current version for {name!r}")
        return self._model_dir(name) / version / "weights.pt"
