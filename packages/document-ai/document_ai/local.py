"""Local, deterministic adapters for development and tests.

NOT FOR PRODUCTION. ``LocalFilesystemStore`` persists objects under a
temporary directory; ``SimulatedOcrEngine`` returns stable canned output
keyed by the content hash so test runs are byte-stable.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Dict

from .base import ExtractedText


class LocalFilesystemStore:
    """Tmp-dir backed ObjectStoreAdapter. Deterministic; not for production."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or tempfile.mkdtemp(prefix="sos-docs-"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        safe_key = key.lstrip("/")
        if ".." in safe_key.split("/"):
            raise ValueError(f"unsafe key: {key!r}")
        return self.root / bucket / safe_key

    def put(self, bucket: str, key: str, data: bytes) -> str:
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, bucket: str, key: str) -> bytes:
        path = self._path(bucket, key)
        if not path.is_file():
            raise KeyError(f"{bucket}/{key}")
        return path.read_bytes()

    def presign(self, bucket: str, key: str, expires_seconds: int = 3600) -> str:
        # Deterministic pseudo-URL for local mode only.
        return f"file://{self._path(bucket, key)}?expires={expires_seconds}"


class SimulatedOcrEngine:
    """Stable canned OCR output keyed by content hash. Not for production."""

    def __init__(self, confidence: float = 0.95, engine_name: str = "simulated-1.0") -> None:
        self.confidence = confidence
        self.engine_name = engine_name

    def extract(self, image_ref: str) -> ExtractedText:
        digest = hashlib.sha256(image_ref.encode("utf-8")).hexdigest()
        lines = [
            f"SIMULATED OCR OUTPUT {digest[:12].upper()}",
            f"DOCUMENT REF {digest[12:24].upper()}",
            "THIS IS DETERMINISTIC CANNED TEXT FOR LOCAL MODE ONLY",
        ]
        return ExtractedText(
            text="\n".join(lines),
            confidence=self.confidence,
            engine=self.engine_name,
            source_hash=image_ref,
            lines=lines,
        )
