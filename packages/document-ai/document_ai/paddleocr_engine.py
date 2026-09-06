"""PaddleOCR engine adapter — optional import, fail closed when unavailable."""
from __future__ import annotations

import os
from typing import Optional

from .base import AdapterUnavailableError, ExtractedText

try:  # optional dependency
    from paddleocr import PaddleOCR  # type: ignore

    _PADDLE_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    PaddleOCR = None  # type: ignore
    _PADDLE_AVAILABLE = False


def _is_production() -> bool:
    return os.environ.get("SOS_ENV", "local").lower() in ("prod", "production", "staging")


class PaddleOcrEngine:
    """Production OCR engine backed by PaddleOCR.

    Fails closed: raises AdapterUnavailableError when ``paddleocr`` is not
    installed, when disabled, or when a production-like environment lacks
    explicit model configuration.
    """

    def __init__(
        self,
        enabled: bool = True,
        language: str = "en",
        model_dir: Optional[str] = None,
    ) -> None:
        self.enabled = enabled
        self.language = language
        self.model_dir = model_dir or os.environ.get("PADDLEOCR_MODEL_DIR")
        self._engine = None
        if not enabled or not _PADDLE_AVAILABLE:
            return
        if _is_production() and not self.model_dir:
            return  # fail closed at extract(): no pinned model in production
        kwargs = {"lang": language, "use_angle_cls": True}  # pragma: no cover
        if self.model_dir:  # pragma: no cover
            kwargs["det_model_dir"] = self.model_dir
        self._engine = PaddleOCR(**kwargs)  # pragma: no cover

    def extract(self, image_ref: str) -> ExtractedText:
        if self._engine is None:
            raise AdapterUnavailableError(
                "PaddleOCR engine unavailable: dependency not installed, disabled, "
                "or production model_dir not configured"
            )
        result = self._engine.ocr(image_ref, cls=True)  # pragma: no cover
        lines = []  # pragma: no cover
        confs = []  # pragma: no cover
        for page in result or []:  # pragma: no cover
            for entry in page or []:
                _box, (text, conf) = entry
                lines.append(text)
                confs.append(float(conf))
        confidence = sum(confs) / len(confs) if confs else 0.0  # pragma: no cover
        return ExtractedText(  # pragma: no cover
            text="\n".join(lines),
            confidence=confidence,
            engine="paddleocr",
            source_hash=image_ref,
            lines=lines,
        )
