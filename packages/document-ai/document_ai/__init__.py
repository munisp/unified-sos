"""packages/document-ai — content-addressed document archive with OCR seams."""
from .base import (
    AdapterUnavailableError,
    ExtractedText,
    ObjectStoreAdapter,
    OcrEngineAdapter,
    PdfSignerAdapter,
    RetentionClass,
    SignedArtifact,
)
from .archive import DocumentArchiveService, IndexLogEntry, ManifestRecord
from .local import LocalFilesystemStore, SimulatedOcrEngine

__all__ = [
    "AdapterUnavailableError",
    "DocumentArchiveService",
    "ExtractedText",
    "IndexLogEntry",
    "LocalFilesystemStore",
    "ManifestRecord",
    "ObjectStoreAdapter",
    "OcrEngineAdapter",
    "PdfSignerAdapter",
    "RetentionClass",
    "SignedArtifact",
    "SimulatedOcrEngine",
]
