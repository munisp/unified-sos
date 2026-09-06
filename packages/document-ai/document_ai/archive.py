"""Deterministic content-addressed document archive.

Keys are content-addressed: ``sha256/<hash>`` objects under the hierarchy
``/<state>/<mda>/<class>/<yyyy>/<hash>``. A hash-chained append-only index
log records every archive operation so the manifest is tamper-evident.

Determinism: no wall-clock or RNG is used implicitly. ``created_at``/year
come from an injected clock (``clock`` callable) and all hashing is SHA-256
over canonical JSON.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional

from .base import (
    AdapterUnavailableError,  # noqa: F401  (re-exported for convenience)
    ObjectStoreAdapter,
    OcrEngineAdapter,
    RetentionClass,
)

DEFAULT_BUCKET = "sos-documents"
GENESIS_HASH = "0" * 64


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(payload: Dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ManifestRecord:
    doc_id: str
    state_id: str
    hash: str
    ocr_text_ref: Optional[str]
    retention: str
    created_at: str


@dataclass(frozen=True)
class IndexLogEntry:
    sequence: int
    record_hash: str
    prev_hash: str
    chain_hash: str
    record: ManifestRecord


class DocumentArchiveService:
    """Content-addressed, hash-chained document archive service."""

    def __init__(
        self,
        store: ObjectStoreAdapter,
        ocr_engine: Optional[OcrEngineAdapter] = None,
        bucket: str = DEFAULT_BUCKET,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self.store = store
        self.ocr_engine = ocr_engine
        self.bucket = bucket
        # Injected clock -> ISO-8601 UTC string. Defaults to a fixed value so
        # archives are byte-stable without explicit configuration.
        self._clock = clock or (lambda: "1970-01-01T00:00:00Z")
        self._index_log: List[IndexLogEntry] = []
        self._manifests: Dict[str, ManifestRecord] = {}
        self._doc_mda: Dict[str, str] = {}

    # ------------------------------------------------------------------ keys
    @staticmethod
    def content_key(digest: str) -> str:
        return f"sha256/{digest}"

    def object_key(
        self, state_id: str, mda: str, retention: RetentionClass, digest: str
    ) -> str:
        created = self._clock()
        year = created[:4]
        return f"/{state_id}/{mda}/{retention.value}/{year}/{digest}"

    # -------------------------------------------------------------- archiving
    def archive(
        self,
        doc_id: str,
        state_id: str,
        mda: str,
        data: bytes,
        retention: RetentionClass,
        run_ocr: bool = True,
    ) -> ManifestRecord:
        if not isinstance(retention, RetentionClass):
            raise ValueError(f"invalid retention class: {retention!r}")
        if not doc_id or not state_id or not mda:
            raise ValueError("doc_id, state_id and mda are required")

        digest = sha256_hex(data)

        # Dedupe by hash: existing manifest short-circuits storage and OCR.
        existing = self._manifests.get(doc_id)
        if existing is not None and existing.hash == digest:
            return existing

        ocr_text_ref: Optional[str] = None
        if run_ocr and self.ocr_engine is not None:
            extracted = self.ocr_engine.extract(self.content_key(digest))
            ocr_key = self.object_key(state_id, mda, retention, digest) + ".ocr.txt"
            self.store.put(self.bucket, ocr_key, extracted.text.encode("utf-8"))
            ocr_text_ref = ocr_key

        key = self.object_key(state_id, mda, retention, digest)
        self.store.put(self.bucket, key, data)

        record = ManifestRecord(
            doc_id=doc_id,
            state_id=state_id,
            hash=digest,
            ocr_text_ref=ocr_text_ref,
            retention=retention.value,
            created_at=self._clock(),
        )
        self._manifests[doc_id] = record
        self._doc_mda[doc_id] = mda
        self._append_index(record)
        return record

    def fetch(self, doc_id: str) -> bytes:
        record = self._manifests[doc_id]
        created = record.created_at
        retention = RetentionClass(record.retention)
        key = f"/{record.state_id}/{self._mda_for(doc_id)}/{retention.value}/{created[:4]}/{record.hash}"
        return self.store.get(self.bucket, key)

    def fetch_by_hash(self, digest: str) -> bytes:
        for doc_id in self._manifests:
            if self._manifests[doc_id].hash == digest:
                return self.fetch(doc_id)
        raise KeyError(digest)

    def manifest(self, doc_id: str) -> ManifestRecord:
        return self._manifests[doc_id]

    def _mda_for(self, doc_id: str) -> str:
        return self._doc_mda[doc_id]

    # ------------------------------------------------------- hash-chained log
    @property
    def index_log(self) -> List[IndexLogEntry]:
        return list(self._index_log)

    def _append_index(self, record: ManifestRecord) -> None:
        record_hash = sha256_hex(_canonical(asdict(record)).encode("utf-8"))
        prev_hash = self._index_log[-1].chain_hash if self._index_log else GENESIS_HASH
        chain_hash = sha256_hex((prev_hash + record_hash).encode("utf-8"))
        self._index_log.append(
            IndexLogEntry(
                sequence=len(self._index_log),
                record_hash=record_hash,
                prev_hash=prev_hash,
                chain_hash=chain_hash,
                record=record,
            )
        )

    def verify_chain(self) -> bool:
        """Recompute the hash chain; True iff every link is intact."""
        prev = GENESIS_HASH
        for i, entry in enumerate(self._index_log):
            if entry.sequence != i or entry.prev_hash != prev:
                return False
            record_hash = sha256_hex(_canonical(asdict(entry.record)).encode("utf-8"))
            if record_hash != entry.record_hash:
                return False
            chain = sha256_hex((prev + record_hash).encode("utf-8"))
            if chain != entry.chain_hash:
                return False
            prev = chain
        return True


