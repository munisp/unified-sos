"""A/B champion/challenger routing for mod-ml-inference.

Deterministic assignment: a stable SHA-256 hash of ``(model, key)`` maps
each request key into [0, 1); keys below ``SOS_ML_CHALLENGER_PCT``/100 are
routed to the challenger version (pointer file in the artifacts dir).
Determinism means the same key always lands on the same variant, which
keeps experiments reproducible and per-entity outcomes comparable.

Every assignment and outcome is appended to a hash-chained log built on
``_shared.hashchain`` (import-guarded like the other shared modules) so the
experiment record is tamper-evident.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- services-root import guard (same idiom as _shared.observability) ----
_SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))

try:
    from _shared.hashchain import (
        GENESIS_PREV_HASH,
        event_payload_hash,
        sha256_hex,
        verify_event_chain,
    )
except ImportError:  # minimal container images ship only the app package
    import hashlib
    import json

    GENESIS_PREV_HASH = "0" * 64

    def _canonical(obj) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

    def sha256_hex(data) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def event_payload_hash(payload, prev_hash: str) -> str:
        body = {k: v for k, v in payload.items()
                if k not in ("prev_hash", "event_hash")}
        body["prev_hash"] = prev_hash
        return sha256_hex(_canonical(body))

    def verify_event_chain(events) -> List[str]:  # pragma: no cover
        errors: List[str] = []
        last = None
        for i, event in enumerate(events):
            prev = event.get("prev_hash")
            expected = GENESIS_PREV_HASH if last is None else last
            if prev != expected:
                errors.append(f"event {i}: broken chain link")
            recomputed = event_payload_hash(event, prev or "")
            if event.get("event_hash") != recomputed:
                errors.append(f"event {i}: hash mismatch")
            last = event.get("event_hash")
        return errors


class ABRouter:
    """Champion/challenger assignment with hash-chained experiment log."""

    def __init__(self, challenger_pct: float = 0.0) -> None:
        self.challenger_pct = max(0.0, min(100.0, float(challenger_pct)))
        self._lock = threading.Lock()
        self.chain: List[Dict] = []
        self._outcomes: Dict[str, List[Dict]] = {}

    # -- deterministic assignment ----------------------------------------------

    def _fraction(self, model_name: str, key: str) -> float:
        digest = sha256_hex(f"ab:{model_name}:{key}")
        return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)

    def assign(self, model_name: str, key: str, champion_version: str,
               challenger_version: Optional[str]) -> Tuple[str, str]:
        """Return ``(variant, version)``; deterministic by ``key``."""
        if (challenger_version and self.challenger_pct > 0
                and self._fraction(model_name, key)
                < self.challenger_pct / 100.0):
            return "challenger", challenger_version
        return "champion", champion_version

    # -- hash-chained logging ------------------------------------------------------

    def _append(self, payload: Dict) -> Dict:
        with self._lock:
            prev_hash = self.chain[-1]["event_hash"] if self.chain else GENESIS_PREV_HASH
            record = dict(payload)
            record["prev_hash"] = prev_hash
            record["event_hash"] = event_payload_hash(record, prev_hash)
            self.chain.append(record)
            return record

    def log_assignment(self, model_name: str, key: str, variant: str,
                       version: str, tenant_state_id: str) -> Dict:
        return self._append({
            "event_id": f"ab-{uuid.uuid4().hex[:16]}",
            "kind": "assignment",
            "model_name": model_name,
            "key_hash": sha256_hex(f"ab:{model_name}:{key}"),
            "variant": variant,
            "version": version,
            "tenant_state_id": tenant_state_id,
            "ts": time.time(),
        })

    def log_outcome(self, model_name: str, variant: str, version: str,
                    prediction_id: str, latency_seconds: float,
                    ok: bool = True) -> Dict:
        record = self._append({
            "event_id": f"ab-{uuid.uuid4().hex[:16]}",
            "kind": "outcome",
            "model_name": model_name,
            "variant": variant,
            "version": version,
            "prediction_id": prediction_id,
            "latency_seconds": round(latency_seconds, 6),
            "ok": bool(ok),
            "ts": time.time(),
        })
        with self._lock:
            self._outcomes.setdefault(model_name, []).append(record)
        return record

    # -- comparison ------------------------------------------------------------------

    def comparison(self, model_name: str) -> Dict:
        with self._lock:
            assignments = [e for e in self.chain
                           if e.get("kind") == "assignment"
                           and e.get("model_name") == model_name]
            outcomes = list(self._outcomes.get(model_name, []))
            chain_errors = verify_event_chain(self.chain)

        def variant_stats(variant: str) -> Dict:
            outs = [o for o in outcomes if o.get("variant") == variant]
            latencies = [o["latency_seconds"] for o in outs]
            return {
                "assignments": sum(1 for a in assignments
                                   if a.get("variant") == variant),
                "outcomes": len(outs),
                "errors": sum(1 for o in outs if not o.get("ok")),
                "mean_latency_seconds": (
                    sum(latencies) / len(latencies) if latencies else 0.0),
            }

        return {
            "model_name": model_name,
            "challenger_pct": self.challenger_pct,
            "variants": {
                "champion": variant_stats("champion"),
                "challenger": variant_stats("challenger"),
            },
            "chain_length": len(self.chain),
            "chain_valid": not chain_errors,
            "chain_errors": chain_errors,
        }


def build_router(env: Optional[Dict[str, str]] = None) -> ABRouter:
    env = dict(os.environ if env is None else env)
    return ABRouter(float(env.get("SOS_ML_CHALLENGER_PCT", "0")))
