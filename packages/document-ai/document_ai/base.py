"""Adapter base types for the document-ai archiving package.

Follows the fail-closed adapter idiom of
``services/mod-kyc-kyb/app/adapters/base.py``: optional production
dependencies are imported behind ``try/except`` and raise
:class:`AdapterUnavailableError` when unavailable or unconfigured.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production adapter dependency is missing."""


class RetentionClass(enum.Enum):
    """Statutory retention classes for archived records."""

    PERMANENT_50YR = "permanent_50yr"
    LONG_TERM_25YR = "long_term_25yr"
    MEDIUM_TERM_7YR = "medium_term_7yr"
    SHORT_TERM_3YR = "short_term_3yr"

    @property
    def years(self) -> int:
        return {
            RetentionClass.PERMANENT_50YR: 50,
            RetentionClass.LONG_TERM_25YR: 25,
            RetentionClass.MEDIUM_TERM_7YR: 7,
            RetentionClass.SHORT_TERM_3YR: 3,
        }[self]


@dataclass(frozen=True)
class ExtractedText:
    """OCR output for a document image."""

    text: str
    confidence: float
    engine: str
    source_hash: str
    lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SignedArtifact:
    """A signed PDF/A rendering of an archived document."""

    doc_ref: str
    artifact_hash: str
    signer: str
    signature_ref: str
    signed_at: str


class ObjectStoreAdapter(Protocol):
    """Content-addressed object storage seam (e.g. MinIO/S3)."""

    def put(self, bucket: str, key: str, data: bytes) -> str:
        """Store ``data`` at ``key``; returns the stored key."""
        ...

    def get(self, bucket: str, key: str) -> bytes:
        """Fetch the object at ``key``; raises KeyError if absent."""
        ...

    def presign(self, bucket: str, key: str, expires_seconds: int = 3600) -> str:
        """Return a time-limited access URL for ``key``."""
        ...


class OcrEngineAdapter(Protocol):
    """OCR seam: extract text from an archived document image."""

    def extract(self, image_ref: str) -> ExtractedText: ...


class PdfSignerAdapter(Protocol):
    """PDF/A signing seam for long-term evidentiary copies."""

    def sign_pdfa(self, doc_ref: str) -> SignedArtifact: ...
