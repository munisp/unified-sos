# mod-police-cad — Public Safety & Emergency Dispatch CAD

**WP-13 / EPIC-15 · Lot 8 · RT-04/05**

Multi-agency incident reporting, computer-aided 112 emergency dispatch, CCTV/drone stream metadata routing, security patrol geofencing, trust-fund administration ledger (TigerBeetle: donations in, disbursements out, publicly auditable).

- **Acceptance:** dispatch latency < 30 s; encrypted tactical channels
- **GATING:** full state-police operational modules gated by constitutional amendment ratification (24-of-36 assemblies + assent); community vigilante CAD (Amotekun, So-Safe, BSCPG, LNSC) operational immediately. Ebubeagu precedent bars armed-force enablement before assent.
- **Design rules (NPSCS lessons):** O&M contracts, due-process certification, asset registries, published uptime
- **Stack:** Rust · PostGIS · Sedona · Kafka · Wazuh (bound via `app/wazuh.py` fail-closed adapter) · WebRTC (bound via `app/webrtc.py` fail-closed adapter)
- **Deploys:** all 6 (Lagos L1 is the certification-reference build)

## The legal gate (Ebubeagu / 24-of-36)

The reference implementation **encodes the gate in code** (`app/gate.py`).
`RatificationGate.ratified` defaults to **FALSE** and is set only from the
certified ratification tally (≥ 24 of 36 State Houses of Assembly **plus**
presidential assent) — never from tenant-level config. While closed, endpoint
categories tagged `ratification_gated` — the **arms register**
(`POST /cad/v1/arms-register`) and **state-force stand-up**
(`POST /cad/v1/state-force/stand-up`) — return **HTTP 423 Locked** with the
legal-basis message (constitutional amendment unratified; **Ebubeagu
precedent**, FHC Abakaliki 14 Feb 2023, bars armed state-force enablement;
NASS certification still pending). Tests cover both gated (423) and
post-ratification (201) behavior.

Ratification-**independent** modules operate immediately: incident intake,
unit/personnel registry with `biometric_enrolled` ghost-worker control,
geofenced dispatch event log, and the security trust-fund
donation/disbursement public audit feed (`GET /cad/v1/trust-fund/{state}/audit-feed`).

## Adapter bindings (fail-closed)

Wazuh and WebRTC are **bound behind fail-closed adapters** with deterministic
fixtures (idiom mirrors mod-erp-bridge / mod-citizen-portal). Selection is via
`SOS_CAD_PROFILE`:

| `SOS_CAD_PROFILE` | WebRTC (`app/webrtc.py`) | Wazuh SIEM (`app/wazuh.py`) |
| --- | --- | --- |
| unset / `fixture` / `local` / `test` (default) | `FixtureWebRTCGateway` — canned SDP answer, in-memory sessions | `FixtureWazuhAdapter` — in-memory, queryable event list |
| `production` / `live` | `AiortcWebRTCGateway` — requires `SOS_WEBRTC_GATEWAY_URL` **and** importable `aiortc`; **hard-fails at boot** (`AdapterUnavailableError`) otherwise | `HttpWazuhAdapter` — POSTs to `SOS_WAZUH_URL` with `SOS_WAZUH_API_TOKEN` bearer; **hard-fails at boot** without both, and at call time on HTTP errors |

Security-relevant events forwarded to the SIEM: `incident_created`,
`dispatch_created`, `gate_denial` (ratification-gate denials are SIEM-visible),
`arms_register_attempt`, `stream_session_opened`, `stream_session_closed`.

### New endpoints

- `POST /cad/v1/streams/{camera_id}/session` — establish a WebRTC session for
  a CCTV/drone feed (`{tenant_state_id, kind: cctv|drone, sdp_offer}`); stream
  metadata (camera id, tenant, kind, `started_at`) is recorded to the domain
  store and forwarded to the SIEM. Media never transits this service.
- `GET /cad/v1/streams[?tenant_state_id=…]` — list active stream sessions
  (tenant filter preserves per-state isolation).
- `DELETE /cad/v1/streams/{session_id}` — close a session (404 on unknown id).
- `GET /cad/v1/dispatch/slo` — dispatch-latency SLO status: `p50_seconds`,
  `p95_seconds`, `breach_count`, `sample_count`, `within_slo` against the
  < 30 s acceptance SLO (`DISPATCH_SLO_SECONDS`, incident-created →
  dispatch-assigned).
- `/metrics` — shared `http_*` exposition plus
  `cad_dispatch_latency_p50_seconds` / `cad_dispatch_latency_p95_seconds`
  gauges and `cad_dispatch_slo_breaches_total` /
  `cad_dispatch_samples_total` counters.

## Reference implementation (Python/FastAPI)

```bash
pip install -e services/mod-police-cad[dev]
uvicorn app.main:app --app-dir services/mod-police-cad --port 8013
# Post-ratification simulation: SOS_POLICE_RATIFIED=true uvicorn app.main:app ...
cd services/mod-police-cad && python3 -m pytest
```
