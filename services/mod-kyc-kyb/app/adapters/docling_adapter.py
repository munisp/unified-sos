"""Docling adapter — optional import, fail closed when unavailable."""
from __future__ import annotations

import time

from ..domain import (
    DocumentArtifact,
    DocumentType,
    ExtractionEngine,
    ExtractionResult,
)
from .base import AdapterUnavailableError

try:  # optional dependency
    from docling.document_converter import DocumentConverter  # type: ignore

    _DOCLING_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    DocumentConverter = None  # type: ignore
    _DOCLING_AVAILABLE = False


class DoclingAdapter:
    """Layout/table-aware document conversion. Fails closed if not installed."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._converter = DocumentConverter() if enabled and _DOCLING_AVAILABLE else None

    def extract(
        self, artifact: DocumentArtifact, document_type: DocumentType
    ) -> ExtractionResult:
        if not self.enabled or self._converter is None:
            raise AdapterUnavailableError(
                "Docling adapter unavailable: dependency not installed or disabled"
            )
        start = time.monotonic()  # pragma: no cover
        result = self._converter.convert(artifact.object_uri)  # pragma: no cover
        doc = result.document  # pragma: no cover
        fields = {}  # pragma: no cover
        tables = getattr(doc, "tables", []) or []
        segments = len(getattr(doc, "texts", []) or [])
        confidence = min(1.0, 0.6 + 0.05 * segments + 0.1 * len(tables))
        return ExtractionResult(  # pragma: no cover
            engine=ExtractionEngine.DOCLING,
            fields=fields,
            confidence=confidence,
            model_version="docling",
            latency_ms=int((time.monotonic() - start) * 1000),
            source_hash=artifact.sha256,
        )
