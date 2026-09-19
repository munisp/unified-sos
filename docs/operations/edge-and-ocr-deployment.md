# Edge Daemon & OCR Sidecar Deployment

Scope: how `edge/edge-daemon` and `packages/document-ai` are deployed (and,
deliberately, where they are *not* deployed).

## 1. edge-daemon — on-device only (no cluster deploy)

`edge/edge-daemon` is an **offline-first edge synchronizer for field
devices** (POS agents, ranger scanners, checkpoint/weighbridge terminals). It
is **not** deployed into the Kubernetes cluster.

Why not in-cluster:

- Its entire reason to exist is **connectivity failure**. It issues
  Ed25519-signed records offline (`se_signer`, `crypto`), persists them in a
  local SQLite outbox (`outbox`, on the device's own storage), and replays
  them with backoff/jitter and idempotent dedupe keys (`sync`) when the link
  returns. Running it next to the brokers it buffers against would remove the
  failure mode it mitigates.
- **Trust boundary:** the signing keys live on the device (secure element
  where available). Co-locating device keys in the cluster would collapse
  the per-device non-repudiation model.
- **Data sovereignty:** unsynced field data (waybills, tickets, readings)
  stays on the originating device until acknowledged — no silent central
  copy.

How it syncs:

1. The daemon signs every record locally and appends it to the outbox.
2. `SyncEngine.sync_all()` pushes pending records in batches to the central
   **sync gateway endpoint** (APISIX gateway → ingestion tier) with
   exponential backoff + jitter; acknowledged records are marked synced
   idempotently (dedupe keys make replays safe).
3. `edge_daemon.gateway` is a **test double** of that server-side protocol —
   do not deploy it as the production gateway.
4. The image (`edge/edge-daemon/Dockerfile`, uvicorn on :8000, SQLite volume
   at `/var/lib/sos-edge`) is built for device-class hosts / edge fleets,
   managed by device management tooling — not by the platform Helm chart
   (which is why `infra/helm/sos-platform/values.yaml` has no edge-daemon
   module).

## 2. packages/document-ai — OCR sidecar for mod-land-docs

`packages/document-ai` (F-049) is the intended **OCR/extraction sidecar**
for `services/mod-land-docs`. mod-land-docs' `PaddleOcrEngine` adapter is
fail-closed on the `SOS_LANDDOCS_OCR_URL` environment variable
(`landdocs_app/adapters.py`):

```
SOS_LANDDOCS_OCR_URL=http://ocr:8080/ocr   # required for profile=paddle/production
```

Deployment model:

- Run document-ai as a **sidecar container in the mod-land-docs pod**
  (same namespace, loopback/cluster-local traffic only; Cilium per-module
  policy in `deploy/cilium/policies/mod-land-docs.yaml` already allows
  same-namespace traffic).
- Build the sidecar image from `packages/document-ai` ( PaddleOCR engine +
  the archive service). A purpose-built Dockerfile for the sidecar is
  follow-up work with the WP-18 pipeline wiring (the package today ships
  adapter seams + tests, not a standalone HTTP server); until then the
  `SOS_LANDDOCS_OCR_URL` contract is the stable integration point.
- Production pinning: `paddleocr` model directory must be pinned
  (`document_ai/paddleocr_engine.py` fails closed otherwise); object
  storage via MinIO/S3 (`document_ai/minio_adapter.py`) with endpoint
  configuration required in prod-like environments (`SOS_ENV`).
- The `document_ai/local.py` simulated engine/store is **dev/test only** —
  never wire it into a production profile.

### Contrast: mod-kyc-kyb

mod-kyc-kyb does **not** use the document-ai sidecar. It integrates its own
document-AI adapters in-process (PaddleOCR + Docling + VLM adjudication,
`services/mod-kyc-kyb/app/adapters/`, same fail-closed idiom). Do not point
mod-kyc-kyb at `SOS_LANDDOCS_OCR_URL`; the two OCR paths are intentionally
separate (different evidence models and NDPA minimization contracts).
