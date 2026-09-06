"""PaddleOCR adapter — optional import, fail closed when unavailable."""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

from ..domain import (
    DocumentArtifact,
    DocumentType,
    ExtractionEngine,
    ExtractionResult,
    sha256_hex,
)
from .base import AdapterUnavailableError

try:  # optional dependency
    from paddleocr import PaddleOCR  # type: ignore

    _PADDLE_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    PaddleOCR = None  # type: ignore
    _PADDLE_AVAILABLE = False


_RC_RE = re.compile(r"\bRC\s*[:#]?\s*(\d{5,8})\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b")
_DOCNO_RE = re.compile(r"\b([A-Z0-9]{9,12})\b")


def parse_ocr_lines(lines: List[str]) -> Dict[str, str]:
    """Best-effort parse of OCR text lines into common fields."""
    fields: Dict[str, str] = {}
    for i, raw in enumerate(lines):
        line = raw.strip()
        low = line.lower()
        if low.startswith(("surname", "last name")) and ":" in line:
            fields["surname"] = line.split(":", 1)[1].strip()
        elif low.startswith(("first name", "given name")) and ":" in line:
            fields["first_name"] = line.split(":", 1)[1].strip()
        elif low.startswith(("document no", "document number", "id no", "passport no")) and ":" in line:
            fields["document_number"] = line.split(":", 1)[1].strip()
        elif low.startswith(("date of birth", "dob")) and ":" in line:
            fields["date_of_birth"] = line.split(":", 1)[1].strip()
        elif low.startswith("expiry") and ":" in line:
            fields["expiry_date"] = line.split(":", 1)[1].strip()
        elif low.startswith("address") and ":" in line:
            fields["address"] = line.split(":", 1)[1].strip()
        elif low.startswith(("business name", "company name", "name of company")) and ":" in line:
            fields["business_name"] = line.split(":", 1)[1].strip()
        rc = _RC_RE.search(line)
        if rc and "rc_number" not in fields:
            fields["rc_number"] = rc.group(1)
        if "document_number" not in fields:
            m = _DOCNO_RE.search(line)
            if m and not any(k in low for k in ("rc", "date")):
                fields.setdefault("document_number", m.group(1))
        if "date_of_birth" not in fields:
            d = _DATE_RE.search(line)
            if d and i > 0:
                fields.setdefault("date_of_birth", d.group(1))
    return fields


class PaddleOCRAdapter:
    """Production OCR adapter. Fails closed if paddleocr is not installed."""

    def __init__(
        self,
        enabled: bool = True,
        language: str = "en",
        model_dir: Optional[str] = None,
    ) -> None:
        self.enabled = enabled
        self.language = language
        self.model_dir = model_dir
        self._engine = None
        if enabled and _PADDLE_AVAILABLE:
            kwargs = {"lang": language, "use_angle_cls": True}
            if model_dir:
                kwargs["det_model_dir"] = model_dir
            self._engine = PaddleOCR(**kwargs)  # pragma: no cover

    def extract(
        self, artifact: DocumentArtifact, document_type: DocumentType
    ) -> ExtractionResult:
        if not self.enabled or self._engine is None:
            raise AdapterUnavailableError(
                "PaddleOCR adapter unavailable: dependency not installed or disabled"
            )
        start = time.monotonic()
        result = self._engine.ocr(artifact.object_uri, cls=True)  # pragma: no cover
        lines: List[str] = []  # pragma: no cover
        confs: List[float] = []
        for page in result or []:  # pragma: no cover
            for entry in page or []:
                _box, (text, conf) = entry
                lines.append(text)
                confs.append(float(conf))
        fields = parse_ocr_lines(lines)  # pragma: no cover
        confidence = sum(confs) / len(confs) if confs else 0.0  # pragma: no cover
        return ExtractionResult(  # pragma: no cover
            engine=ExtractionEngine.PADDLEOCR,
            fields=fields,
            confidence=confidence,
            model_version="paddleocr",
            latency_ms=int((time.monotonic() - start) * 1000),
            source_hash=artifact.sha256,
        )


class SimulatedDocumentAIAdapter:
    """Deterministic adapter for local/test mode only.

    Fields are derived deterministically from the artifact hash so tests are
    reproducible. Never use in production.
    """

    def __init__(
        self,
        engine: ExtractionEngine = ExtractionEngine.SIMULATED,
        confidence: float = 0.95,
        mismatch_with: Optional[str] = None,
    ) -> None:
        self.engine = engine
        self.confidence = confidence
        self.mismatch_with = mismatch_with  # force consensus mismatch when set

    def extract(
        self, artifact: DocumentArtifact, document_type: DocumentType
    ) -> ExtractionResult:
        seed = artifact.sha256
        doc_number = "DOC" + seed[:9].upper()
        if self.mismatch_with:
            doc_number = "MISM" + seed[:8].upper()
        fields: Dict[str, str] = {
            "document_number": doc_number,
            "surname_hash": sha256_hex("surname:" + seed)[:16],
            "first_name_hash": sha256_hex("first:" + seed)[:16],
            "date_of_birth_hash": sha256_hex("dob:" + seed)[:16],
        }
        if document_type == DocumentType.CAC_CERTIFICATE:
            fields["rc_number"] = "RC" + str(int(seed[:6], 16) % 9000000 + 1000000)
        return ExtractionResult(
            engine=self.engine,
            fields=fields,
            confidence=self.confidence,
            model_version="simulated-1.0",
            latency_ms=1,
            source_hash=artifact.sha256,
        )
