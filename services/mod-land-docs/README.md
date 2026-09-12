# mod-land-docs — Land Document Management + Intelligent OCR

Land document registry for state lands bureaus: upload, rule-based
classification, OCR extraction, verification, versioning, duplicate
detection, and a hash-chained audit trail. All state is tenant-scoped by the
`X-State-Tenant` header (400 when missing on `/api/` routes, 404 across
tenants). Deterministic fixture engines run by default; production engines
sit behind fail-closed adapter seams.

## Lifecycle

`REGISTERED → CLASSIFIED → OCR_EXTRACTED → VERIFIED / REJECTED`, with
`VERIFIED → ARCHIVED`; uploading a new version marks the prior head
`SUPERSEDED`. Illegal transitions return HTTP 409. OCR confidence < 0.7 sets
`needs_manual_review`.

## Endpoints

All under `/api/v1/states/{state_id}/land-docs`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/documents` | register + store (returns duplicate warnings) |
| POST | `/documents/{id}/classify` | rule-based classification |
| POST | `/documents/{id}/ocr` | run OCR engine, store fields + confidence |
| POST | `/documents/{id}/verify` | verify (requires verifier + reason) |
| POST | `/documents/{id}/reject` | reject (requires reason) |
| POST | `/documents/{id}/versions` | upload a new version (supersedes prior) |
| GET  | `/documents` | list, filter by `doc_type` / `status` / `parcel_id` |
| GET  | `/documents/{id}` | document + audit chain |
| GET  | `/documents/{id}/audit` | hash-chain verification (`valid: true/false`) |
| GET  | `/healthz` | liveness |
| GET  | `/metrics` | Prometheus counters (docs registered, OCR runs, verifications) |

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `SOS_LANDDOCS_PROFILE` | `development` | `production` hard-requires OCR config (fail-closed boot) |
| `SOS_LANDDOCS_OCR_ENGINE` | `fixture` | `fixture` (deterministic) or `paddle` |
| `SOS_LANDDOCS_OCR_URL` | unset | PaddleOCR sidecar URL (HTTP multipart POST); required for `paddle` and in production |
| `SOS_LANDDOCS_BUCKET` | unset | S3-compatible bucket; unset → in-memory fixture object store |

## Production seams

- `PaddleOcrEngine` — raises `AdapterUnavailableError` without
  `SOS_LANDDOCS_OCR_URL`; in `SOS_LANDDOCS_PROFILE=production` the app
  refuses to boot without it.
- `S3ObjectStore` — raises `AdapterUnavailableError` without
  `SOS_LANDDOCS_BUCKET`.

## Events

Published on the shared in-process bus (`services/_shared/eventbus`):
`ng.sos.landdocs.document_registered`, `ng.sos.landdocs.ocr_completed`,
`ng.sos.landdocs.document_verified`, `ng.sos.landdocs.duplicate_suspected`.

## Tests

```sh
cd services/mod-land-docs
python3 -m pytest tests/ -q
```
