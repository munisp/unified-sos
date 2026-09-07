"""Continuous-training orchestrator (champion/challenger).

Watches a lakehouse extract directory for new data (poll interval), retrains
on a sliding window of recent extracts, and promotes a challenger only when
its validation metric beats the production champion by at least
``promotion_margin``. Promoted weights + model card are written through the
registry (ml/registry.py) and mirrored into the artifact contract layout.

Usage:
    python -m ml.training.continuous --model credit_mlp --watch-dir <dir> \\
        --metric test_auc --margin 0.005 --poll-interval 60 --once
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ml.registry import ModelRegistry
from ml.training import train as trainer

#: Higher-is-better metrics per model (promotion compares these).
PRIMARY_METRIC = {"fraud_gnn": "test_auc", "credit_mlp": "test_auc",
                  "luc_avm": "test_r2", "crowd_lstm": "test_mse"}
LOWER_IS_BETTER = {"test_mse", "val_loss", "test_mae"}


@dataclass
class ChampionChallenger:
    """Promotion decision: challenger must beat champion by margin."""

    margin: float = 0.005
    history: list[dict] = field(default_factory=list)

    def should_promote(self, metric: str, champion: float | None,
                       challenger: float) -> bool:
        if champion is None:
            decision = True
        elif metric in LOWER_IS_BETTER:
            decision = challenger < champion - self.margin
        else:
            decision = challenger > champion + self.margin
        self.history.append({"metric": metric, "champion": champion,
                             "challenger": challenger, "promoted": decision})
        return decision


class ContinuousTrainer:
    def __init__(self, watch_dir: Path, registry: ModelRegistry | None = None,
                 margin: float = 0.005, epochs: int = 5, seed: int = 42,
                 artifacts_dir: Path | None = None):
        self.watch_dir = Path(watch_dir)
        self.registry = registry or ModelRegistry()
        self.gate = ChampionChallenger(margin=margin)
        self.epochs = epochs
        self.seed = seed
        self.artifacts_dir = artifacts_dir
        self._seen: set[str] = set()

    def _new_extracts(self) -> list[Path]:
        if not self.watch_dir.exists():
            return []
        files = sorted(self.watch_dir.glob("*.parquet")) + sorted(self.watch_dir.glob("*.csv"))
        return [f for f in files if f.name not in self._seen]

    def check_once(self, model_name: str, metric: str | None = None) -> dict | None:
        """Poll once; retrain + maybe promote when new extracts appeared."""
        new = self._new_extracts()
        if not new:
            return None
        self._seen.update(f.name for f in new)
        metric = metric or PRIMARY_METRIC[model_name]

        result = trainer.train_one(model_name, self.epochs, self.seed,
                                   data_dir=None, artifacts_dir=self.artifacts_dir)
        challenger = result["metrics"][metric]

        champion: float | None = None
        try:
            champion = self.registry.load_card(model_name)["metrics"].get(metric)
        except Exception:
            pass

        if self.gate.should_promote(metric, champion, challenger):
            artifact = Path(result["artifact"])
            card = json.loads((artifact / "model_card.json").read_text())
            version = self.registry.register(model_name, artifact / "weights.pt",
                                             card, promote=True)
            return {"model": model_name, "promoted": True, "version": version,
                    "metric": metric, "challenger": challenger, "champion": champion,
                    "extracts": [f.name for f in new]}
        return {"model": model_name, "promoted": False, "metric": metric,
                "challenger": challenger, "champion": champion,
                "extracts": [f.name for f in new]}

    def run(self, model_name: str, poll_interval: float = 60.0,
            metric: str | None = None, max_cycles: int | None = None) -> None:
        cycles = 0
        while True:
            out = self.check_once(model_name, metric)
            if out:
                print(json.dumps(out))
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Continuous-training orchestrator.")
    p.add_argument("--model", required=True, choices=trainer.MODEL_NAMES)
    p.add_argument("--watch-dir", type=Path, required=True)
    p.add_argument("--metric", default=None)
    p.add_argument("--margin", type=float, default=0.005)
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--poll-interval", type=float, default=60.0)
    p.add_argument("--once", action="store_true")
    args = p.parse_args(argv)
    ct = ContinuousTrainer(args.watch_dir, margin=args.margin, epochs=args.epochs)
    if args.once:
        out = ct.check_once(args.model, args.metric)
        print(json.dumps(out or {"status": "no new extracts"}))
    else:
        ct.run(args.model, args.poll_interval, args.metric)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
