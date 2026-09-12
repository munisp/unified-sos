"""Title-risk scoring seam (fail-closed adapter idiom).

Deterministic fixture scorer is the default everywhere; a production HTTP
scorer (mod-ml-inference fraud model) sits behind the
:class:`TitleRiskAdapter` protocol and is selected via environment::

    SOS_LANDS_PROFILE=dev|production   (default: dev)
    SOS_LANDS_RISK_URL=http://mod-ml-inference:8021/ml/v1/fraud/score

Fail-closed: when ``SOS_LANDS_PROFILE=production`` and ``SOS_LANDS_RISK_URL``
is unset, :func:`risk_scorer_from_env` raises
:class:`AdapterUnavailableError` at service boot rather than silently falling
back to the fixture — mirroring the mod-safecity-vision adapter idiom.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

#: Default in-cluster URL of the ML fraud-scoring endpoint (deployment config).
DEFAULT_RISK_URL = "http://mod-ml-inference:8021/ml/v1/fraud/score"


class AdapterUnavailableError(RuntimeError):
    """Raised when a production adapter dependency/config is missing."""


@dataclass(frozen=True)
class RiskFactor:
    """One scored risk signal (e.g. open dispute, rapid transfers)."""

    kind: str
    detail: str
    weight: int


@dataclass(frozen=True)
class RiskAssessment:
    """Score + contributing factors returned by the risk endpoint."""

    parcel_id: str
    score: int  # 0-100
    factors: List[RiskFactor]
    scorer: str

    def as_dict(self) -> dict:
        return {
            "parcel_id": self.parcel_id,
            "score": self.score,
            "scorer": self.scorer,
            "factors": [
                {"kind": f.kind, "detail": f.detail, "weight": f.weight}
                for f in self.factors
            ],
        }


class TitleRiskAdapter(Protocol):
    """Adapter seam: parcel id + observed factors -> 0-100 risk assessment."""

    def score(
        self, *, tenant_state_id: str, parcel_id: str, factors: List[RiskFactor]
    ) -> RiskAssessment: ...


class FixtureTitleRiskScorer:
    """Deterministic fixture scorer (default; local dev and tests).

    Base risk is derived from SHA-256 of the parcel id (0-50), so the same
    parcel always scores the same; observed factors add their weights
    (open dispute +30, rapid successive transfers +15, superseded lineage
    gaps +20) and the total is clamped to 100.
    """

    name = "fixture"

    def _base(self, parcel_id: str) -> int:
        return int(hashlib.sha256(parcel_id.encode("utf-8")).hexdigest()[:8], 16) % 51

    def score(
        self, *, tenant_state_id: str, parcel_id: str, factors: List[RiskFactor]
    ) -> RiskAssessment:
        base = self._base(parcel_id)
        total = base + sum(f.weight for f in factors)
        return RiskAssessment(
            parcel_id=parcel_id,
            score=max(0, min(100, total)),
            factors=list(factors),
            scorer=self.name,
        )


class HttpTitleRiskScorer:
    """Production scorer — POSTs to the mod-ml-inference fraud endpoint.

    Fail-closed: any transport/dependency failure raises
    :class:`AdapterUnavailableError` instead of returning a fabricated score.
    """

    name = "http"

    def __init__(
        self,
        base_url: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None,
        timeout_s: float = 5.0,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.base_url = base_url or env.get("SOS_LANDS_RISK_URL") or DEFAULT_RISK_URL
        self.timeout_s = timeout_s

    def score(
        self, *, tenant_state_id: str, parcel_id: str, factors: List[RiskFactor]
    ) -> RiskAssessment:  # pragma: no cover - requires live ML service
        try:
            import httpx
        except ImportError as exc:
            raise AdapterUnavailableError(
                "httpx is required for HttpTitleRiskScorer"
            ) from exc
        payload = {
            "tenant_state_id": tenant_state_id,
            "parcel_id": parcel_id,
            "factors": [{"kind": f.kind, "detail": f.detail, "weight": f.weight} for f in factors],
        }
        try:
            resp = httpx.post(self.base_url, json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            body = resp.json()
            return RiskAssessment(
                parcel_id=parcel_id,
                score=max(0, min(100, int(body["score"]))),
                factors=list(factors),
                scorer=self.name,
            )
        except Exception as exc:
            raise AdapterUnavailableError(
                f"title-risk scorer unavailable at {self.base_url}: {exc} "
                "(fail-closed: refusing to fabricate a risk score)"
            ) from exc


def risk_scorer_from_env(environ: Optional[Dict[str, str]] = None) -> TitleRiskAdapter:
    """Build the risk scorer from environment (fixture default; fail-closed prod)."""
    env = environ if environ is not None else dict(os.environ)
    profile = env.get("SOS_LANDS_PROFILE", "dev")
    url = env.get("SOS_LANDS_RISK_URL")
    if profile == "production":
        if not url:
            raise AdapterUnavailableError(
                "SOS_LANDS_RISK_URL is required when SOS_LANDS_PROFILE=production "
                "(fail-closed: refusing to boot with the fixture risk scorer)"
            )
        return HttpTitleRiskScorer(url)
    if url:
        return HttpTitleRiskScorer(url)
    return FixtureTitleRiskScorer()
