"""Tests for the document-ai archive package.

Deterministic: an injected fixed clock is used; no wall-clock or RNG.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from document_ai import (  # noqa: E402
    AdapterUnavailableError,
    DocumentArchiveService,
    LocalFilesystemStore,
    RetentionClass,
    SimulatedOcrEngine,
)
from document_ai.archive import GENESIS_HASH  # noqa: E402
from document_ai.minio_adapter import MinioObjectStoreAdapter  # noqa: E402
from document_ai.paddleocr_engine import PaddleOcrEngine  # noqa: E402

FIXED_NOW = "2024-03-01T00:00:00Z"


def make_service(tmp_path, **kwargs):
    store = LocalFilesystemStore(root=str(tmp_path / "objects"))
    ocr = kwargs.pop("ocr_engine", SimulatedOcrEngine())
    return DocumentArchiveService(
        store=store, ocr_engine=ocr, clock=lambda: FIXED_NOW, **kwargs
    )


def test_put_get_round_trip(tmp_path):
    svc = make_service(tmp_path)
    data = b"deed scan page 1"
    rec = svc.archive(
        "doc-1", "lagos", "lands-registry", data, RetentionClass.PERMANENT_50YR
    )
    assert svc.fetch("doc-1") == data
    assert rec.doc_id == "doc-1"
    assert rec.state_id == "lagos"
    assert rec.created_at == FIXED_NOW
    assert rec.retention == RetentionClass.PERMANENT_50YR.value
    assert len(rec.hash) == 64


def test_content_addressed_key_hierarchy(tmp_path):
    svc = make_service(tmp_path)
    rec = svc.archive(
        "doc-1", "lagos", "lands-registry", b"payload", RetentionClass.MEDIUM_TERM_7YR
    )
    expected = f"/lagos/lands-registry/medium_term_7yr/2024/{rec.hash}"
    assert svc.store.get(svc.bucket, expected) == b"payload"


def test_dedupe_by_hash(tmp_path):
    svc = make_service(tmp_path)
    r1 = svc.archive("doc-1", "lagos", "lands-registry", b"same", RetentionClass.SHORT_TERM_3YR)
    r2 = svc.archive("doc-1", "lagos", "lands-registry", b"same", RetentionClass.SHORT_TERM_3YR)
    assert r1 == r2
    assert len(svc.index_log) == 1  # second archive was a no-op


def test_ocr_seam_delegation(tmp_path):
    svc = make_service(tmp_path)
    rec = svc.archive("doc-ocr", "kano", "dgit", b"scanned form", RetentionClass.LONG_TERM_25YR)
    assert rec.ocr_text_ref is not None
    text = svc.store.get(svc.bucket, rec.ocr_text_ref).decode("utf-8")
    assert "SIMULATED OCR OUTPUT" in text
    # Stable canned output keyed by hash -> deterministic across reruns
    svc2 = make_service(tmp_path / "rerun")
    rec2 = svc2.archive("doc-ocr", "kano", "dgit", b"scanned form", RetentionClass.LONG_TERM_25YR)
    assert svc2.store.get(svc2.bucket, rec2.ocr_text_ref).decode("utf-8") == text


def test_ocr_can_be_disabled(tmp_path):
    svc = make_service(tmp_path)
    rec = svc.archive(
        "doc-noocr", "lagos", "lands-registry", b"blob", RetentionClass.SHORT_TERM_3YR,
        run_ocr=False,
    )
    assert rec.ocr_text_ref is None


def test_retention_validation(tmp_path):
    svc = make_service(tmp_path)
    with pytest.raises(ValueError):
        svc.archive("doc-x", "lagos", "mda", b"blob", "not-a-class")
    with pytest.raises(ValueError):
        svc.archive("", "lagos", "mda", b"blob", RetentionClass.SHORT_TERM_3YR)


def test_hash_chain_verification(tmp_path):
    svc = make_service(tmp_path)
    for i in range(4):
        svc.archive(
            f"doc-{i}", "lagos", "mda", f"payload-{i}".encode(), RetentionClass.MEDIUM_TERM_7YR
        )
    log = svc.index_log
    assert log[0].prev_hash == GENESIS_HASH
    assert svc.verify_chain()

    # Tamper with the chain -> verification fails
    entry = log[1]
    svc._index_log[1] = type(entry)(
        sequence=entry.sequence,
        record_hash="ff" * 32,
        prev_hash=entry.prev_hash,
        chain_hash=entry.chain_hash,
        record=entry.record,
    )
    assert not svc.verify_chain()


def test_deterministic_byte_stability(tmp_path):
    outputs = []
    for run in range(2):
        svc = make_service(tmp_path / f"run{run}")
        svc.archive("doc-a", "oyo", "agric", b"steady", RetentionClass.PERMANENT_50YR)
        entry = svc.index_log[0]
        outputs.append((entry.record_hash, entry.chain_hash, entry.record.created_at))
    assert outputs[0] == outputs[1]


def test_minio_adapter_fails_closed(tmp_path, monkeypatch):
    # No minio package and/or no config -> put/get/presign must raise.
    adapter = MinioObjectStoreAdapter(endpoint=None, access_key=None, secret_key=None)
    with pytest.raises(AdapterUnavailableError):
        adapter.put("b", "k", b"x")
    with pytest.raises(AdapterUnavailableError):
        adapter.get("b", "k")
    with pytest.raises(AdapterUnavailableError):
        adapter.presign("b", "k")
    # Production env without config still fails closed.
    monkeypatch.setenv("SOS_ENV", "production")
    adapter2 = MinioObjectStoreAdapter()
    with pytest.raises(AdapterUnavailableError):
        adapter2.get("b", "k")


def test_paddleocr_engine_fails_closed(monkeypatch):
    engine = PaddleOcrEngine()
    if engine._engine is None:  # paddleocr absent in this env
        with pytest.raises(AdapterUnavailableError):
            engine.extract("sha256/" + "ab" * 32)
    disabled = PaddleOcrEngine(enabled=False)
    with pytest.raises(AdapterUnavailableError):
        disabled.extract("sha256/" + "cd" * 32)
