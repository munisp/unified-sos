# mod-safecity-vision — Safe-City AI/ML/DL/CV Analytics

Next-generation analytics service for state Safe-City CCTV/drone camera
estates across all six tenant states (Lagos, Ogun, Osun, Benue, Nasarawa,
Taraba).

- **Face recognition** — watchlist enrolment (128-d embeddings,
  integer-quantized for fixtures) and cosine-similarity matching with a
  configurable threshold. Adapter seam `FaceEngineAdapter`: deterministic
  `FixtureFaceEngine` by default; `InsightFaceEngine` (ArcFace) behind
  `SOS_VISION_FACE_ENGINE=insightface` + `SOS_VISION_MODEL_DIR`, fail-closed
  (`AdapterUnavailableError`) without configuration.
- **Crowd monitoring / density estimation** — per-camera crowd count,
  density (persons/m²), flow rate, and threshold-based stampede-risk alerts
  with a configurable per-camera `crowd_density_threshold`.
- **Anomaly detection** — loitering, perimeter breach (geofence polygon),
  object-left-behind, running and stampede; deterministic fixture rules by
  default, rule+ML seam for production models.
- **Stream registry** — cameras registered per tenant with id, location
  (lat/lon), `webrtc://`/`rtsp://` stream URI and capabilities.
- **Events** — `ng.sos.safecity.face_match`, `ng.sos.safecity.crowd_alert`,
  `ng.sos.safecity.anomaly_detected` published via the shared
  `services/_shared/eventbus` idiom (in-memory default).
- **Stack:** Python 3.11+ · FastAPI · pydantic v2
- **Multi-tenancy:** every request is scoped by the `X-State-Tenant` header.

## The legal gate (NDPA 2023)

Biometric data is *sensitive personal data* under the **Nigeria Data
Protection Act 2023 (ss. 25, 30)** — processing requires an explicit lawful
basis; consent alone is insufficient for state surveillance. The reference
implementation **encodes the gate in code** (`app/gate.py`):
`AuthorizationGate` is closed by default and opens per tenant only when a
**certified authorization record** (judicial warrant ref or DPO approval
ref, with a valid expiry) is loaded from a certified source
(`SOS_VISION_AUTHORIZATIONS_FILE` JSON, provisioned by the governance
control plane) — **never from tenant config**. While closed, the
`biometric_gated` endpoints (`POST /vision/v1/faces/enroll`,
`POST /vision/v1/faces/match`, `GET /vision/v1/faces/audit`) return
**HTTP 423 Locked** with the legal-basis message.

Crowd-monitoring and anomaly-detection endpoints process no biometrics and
are **not gated**. Every face lookup is appended to a **hash-chained audit
log** (`services/_shared/hashchain`, tamper-evident), including the
authorization ref under which it ran.

## Reference implementation (Python/FastAPI)

```bash
pip install -e services/mod-safecity-vision[dev]
uvicorn app.main:app --app-dir services/mod-safecity-vision --port 8014
# With a certified authorization source:
SOS_VISION_AUTHORIZATIONS_FILE=/etc/sos/vision-authorizations.json \
    uvicorn app.main:app --app-dir services/mod-safecity-vision --port 8014
cd services/mod-safecity-vision && python3 -m pytest
```

`/healthz` is always available; `/metrics` (zero-dependency Prometheus text
exposition) is wired via `services/_shared/observability.py` when the
shared package is importable, mirroring mod-police-cad.
