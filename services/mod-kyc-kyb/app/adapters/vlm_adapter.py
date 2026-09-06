"""VLM adjudication adapter — vendor-neutral seam, fail closed."""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional
from urllib import request as urlrequest

from ..domain import (
    DocumentArtifact,
    DocumentType,
    ExtractionEngine,
    ExtractionResult,
    sha256_hex,
)
from .base import AdapterUnavailableError


class VLMAdapter:
    """Adjudicates OCR/Docling fields via an OpenAI-compatible VLM endpoint.

    No hardcoded vendor: supply ``endpoint_url`` for any compatible server.
    If no endpoint and no local model callable are provided, the adapter is
    unavailable and fails closed.
    """

    def __init__(
        self,
        enabled: bool = True,
        endpoint_url: Optional[str] = None,
        model: str = "vlm-adjudicator",
        prompt_policy_version: str = "v1",
        local_model=None,
        timeout_s: float = 10.0,
    ) -> None:
        self.enabled = enabled
        self.endpoint_url = endpoint_url
        self.model = model
        self.prompt_policy_version = prompt_policy_version
        self._local_model = local_model
        self.timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return self.enabled and (self.endpoint_url is not None or self._local_model is not None)

    def extract(
        self,
        artifact: DocumentArtifact,
        document_type: DocumentType,
        prior_fields: Optional[Dict[str, str]] = None,
    ) -> ExtractionResult:
        if not self.available:
            raise AdapterUnavailableError(
                "VLM adapter unavailable: no endpoint or local model configured"
            )
        start = time.monotonic()
        prior_fields = prior_fields or {}
        if self._local_model is not None:
            raw = self._local_model(artifact.object_uri, document_type.value, prior_fields)
        else:  # pragma: no cover - network path
            body = json.dumps(
                {
                    "model": self.model,
                    "prompt_policy_version": self.prompt_policy_version,
                    "object_uri": artifact.object_uri,
                    "document_type": document_type.value,
                    "prior_fields": prior_fields,
                }
            ).encode("utf-8")
            req = urlrequest.Request(
                self.endpoint_url, data=body,
                headers={"Content-Type": "application/json"},
            )
            with urlrequest.urlopen(req, timeout=self.timeout_s) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        fields: Dict[str, str] = dict(raw.get("fields", {}))
        indicators: List[str] = list(raw.get("tamper_indicators", []))
        rationale = str(raw.get("rationale", ""))
        warnings = [f"tamper:{i}" for i in indicators]
        # Compare VLM output against OCR/Docling fields and surface mismatches.
        for key, prior in prior_fields.items():
            vlm_val = fields.get(key)
            if vlm_val is not None and prior and vlm_val != prior:
                warnings.append(f"mismatch:{key}")
        return ExtractionResult(
            engine=ExtractionEngine.VLM,
            fields=fields,
            confidence=float(raw.get("confidence", 0.0)),
            model_version=f"{self.model}/{self.prompt_policy_version}",
            latency_ms=int((time.monotonic() - start) * 1000),
            warnings=warnings,
            source_hash=sha256_hex(rationale) if rationale else artifact.sha256,
        )


class SimulatedVLMAdapter:
    """Deterministic VLM seam for local/test mode."""

    def __init__(self, confidence: float = 0.9, mismatch_keys: Optional[List[str]] = None):
        self.confidence = confidence
        self.mismatch_keys = mismatch_keys or []

    def extract(
        self,
        artifact: DocumentArtifact,
        document_type: DocumentType,
        prior_fields: Optional[Dict[str, str]] = None,
    ) -> ExtractionResult:
        prior_fields = prior_fields or {}
        fields = dict(prior_fields)
        for key in self.mismatch_keys:
            if key in fields:
                fields[key] = "VLM-DIFFERENT"
        warnings = [f"mismatch:{k}" for k in self.mismatch_keys if k in prior_fields]
        return ExtractionResult(
            engine=ExtractionEngine.VLM,
            fields=fields,
            confidence=self.confidence,
            model_version="simulated-vlm/1.0",
            latency_ms=1,
            warnings=warnings,
            source_hash=sha256_hex("rationale:" + artifact.sha256),
        )
