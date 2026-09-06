# Lagos State Policy Pack

**Tier:** 1 — Dedicated Sovereign · **Readiness:** 93.0 (Mature) · **Wave:** 0/1

**Baseline [LIVE]:** Pop. 22.0M+; IGR ₦1.26trn (2024, #1 nationally, ~35% of
subnational IGR); 2026 IGR target ₦3.119trn. Dedicated K8s cluster, standalone
TigerBeetle cluster, dedicated MinIO buckets; hosting concession-funded
($25k–$45k/mo).

## Revenue split (`policy-pack.json`)

Head: `REV_MULTIMODAL_TRANSIT_TICKETING` (unified multimodal transit clearing).
Includes the Lagos-specific statutory beneficiaries:

- `SECURITY_TRUST_FUND` — Lagos State Security Trust Fund (LSSTF), Nigeria's
  first statutory PPP security trust fund (2007) [LIVE] — account 4001, 4% INSTANT.
- `TRANSPORT_UNION_COMMISSION` — road transport union welfare commission pool
  (union auto-splits of 3–8% are written into payout rules per the resilience
  doc) — account 4002, 6% INSTANT.

Concession ceiling: **8%** (Lagos/Ogun class, procurement guardrails).

## Enabled modules (`modules.yaml`)

| Module | Wave | Driver |
|---|---|---|
| `mod-rev-core` | 1 | L4 — LAWMA PSP billing digitization (fastest, least-political entry) |
| `mod-mobility-switch` | 1 | #26 — multimodal transit clearing (Cowry Gen 2) |
| `mod-gis-luc` | 1 | #27 — high-density cadastre & 3D LUC AI |
| `mod-police-cad` | 2 | #29 — Safe City traffic & space violations |
| `mod-transport-wim` | 2 | #28 — port access smart corridor (Lekki/Apapa) |
| `mod-agri-waybill` | 3 | #30 — waterways & wharf cargo |

## Key risks (from `docs/states/lagos.md`)

- EndSARS toll legacy — collection-integrity framing only, never "tolling".
- Sophisticated counterparty — LIRS benchmarks globally.
- Safe City history — governance/oversight is the product differentiator.
