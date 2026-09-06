# mod-police-cad — Public Safety & Emergency Dispatch CAD

**WP-13 / EPIC-15 · Lot 8 · RT-04/05**

Multi-agency incident reporting, computer-aided 112 emergency dispatch, CCTV/drone stream metadata routing, security patrol geofencing, trust-fund administration ledger (TigerBeetle: donations in, disbursements out, publicly auditable).

- **Acceptance:** dispatch latency < 30 s; encrypted tactical channels
- **GATING:** full state-police operational modules gated by constitutional amendment ratification (24-of-36 assemblies + assent); community vigilante CAD (Amotekun, So-Safe, BSCPG, LNSC) operational immediately. Ebubeagu precedent bars armed-force enablement before assent.
- **Design rules (NPSCS lessons):** O&M contracts, due-process certification, asset registries, published uptime
- **Stack:** Rust · PostGIS · Sedona · Kafka · Wazuh · WebRTC
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

## Reference implementation (Python/FastAPI)

```bash
pip install -e services/mod-police-cad[dev]
uvicorn app.main:app --app-dir services/mod-police-cad --port 8013
# Post-ratification simulation: SOS_POLICE_RATIFIED=true uvicorn app.main:app ...
cd services/mod-police-cad && python3 -m pytest
```
