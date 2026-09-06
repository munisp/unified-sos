# packages/document-ai — F-049 document & OCR archiving

Content-addressed document archive with OCR and PDF/A signing seams,
following the fail-closed adapter idiom of
`services/mod-kyc-kyb/app/adapters/base.py`.

## Layout

- `document_ai/base.py` — adapter protocols (`ObjectStoreAdapter`,
  `OcrEngineAdapter`, `PdfSignerAdapter`), `AdapterUnavailableError`,
  `RetentionClass` (PERMANENT_50YR, LONG_TERM_25YR, MEDIUM_TERM_7YR,
  SHORT_TERM_3YR).
- `document_ai/archive.py` — `DocumentArchiveService`: content-addressed keys
  (`sha256/<hash>`) under the hierarchy `/<state>/<mda>/<class>/<yyyy>/<hash>`,
  manifest records `{doc_id, state_id, hash, ocr_text_ref, retention,
  created_at}`, and a hash-chained append-only index log
  (`verify_chain()` for tamper-evidence).
- `document_ai/local.py` — `LocalFilesystemStore` (tmp-dir backed) and
  `SimulatedOcrEngine` (stable canned output keyed by content hash).
  **Not for production.**
- `document_ai/minio_adapter.py` — MinIO/S3 object store. Optional import;
  raises `AdapterUnavailableError` when the `minio` package is missing or a
  production-like environment (`SOS_ENV` in prod/production/staging) lacks
  endpoint configuration.
- `document_ai/paddleocr_engine.py` — PaddleOCR engine. Optional import;
  fails closed when `paddleocr` is missing, disabled, or production
  `model_dir` is not pinned.

## Determinism

`DocumentArchiveService` takes an injected `clock` callable; without one it
uses a fixed epoch timestamp, so archives and the hash chain are byte-stable
across runs. No wall-clock or RNG is used implicitly.

## Tests

```
python3 -m pytest packages/document-ai/tests -q
```

## Status

F-049 seam package (P2 / Stage 4.3). Production MinIO + PaddleOCR wiring and
PDF/A signing land with the full WP-18 pipeline.
