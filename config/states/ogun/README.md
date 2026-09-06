# Ogun State Policy Pack

**Tier:** 2 — Hybrid Industrial · **Readiness:** 83.0 (Very High) · **Wave:** 1

**Baseline [LIVE]:** Pop. 6.5M; IGR ₦146.8bn (2024, #4 nationally, +32.7% YoY);
2026 budget ₦1.668trn. Dedicated PostgreSQL/Redis transactional plane + shared
analytical lakehouse; hosting concession-funded ($10k–$18k/mo). Anchor: leading
industrial & manufacturing corridor (Ota/Sagamu/Agbara).

## Revenue split (`policy-pack.json`)

Head: `REV_LAND_USE_CHARGE` — mirrors the canonical example
[`contracts/policy-packs/examples/ogun-luc-2026.json`](../../../contracts/policy-packs/examples/ogun-luc-2026.json):
CRF 75% / Bureau of Lands retention 15% / concessionaire escrow 8% INSTANT,
LG share pool 2% END_OF_MONTH.

Concession ceiling: **8%** (Lagos/Ogun class, procurement guardrails).

## Enabled modules (`modules.yaml`)

| Module | Wave | Driver |
|---|---|---|
| `mod-transport-wim` | 1 | #18 — haulage corridor WIM & RFID |
| `mod-rev-core` | 1 | #19/O3 — auto-ticketing + OGIRS informal-sector collection |
| `mod-forestry` | 2 | #16 — industrial emissions & effluent IoT (OGEPA) |
| `mod-gis-lands` | 2 | #17/O2 — industrial land titling (OLARMS) |
| `mod-gis-luc` | 3 | #20 — digital building approvals & LUC |

## Key risks (from `docs/states/ogun.md`)

- IGR-target credibility — baselines from measured collections only.
- Quarry-levy politics litigious — platform is the neutral audit layer.
- Federal-interface exposure at Idiroko border — state-competent levies only.
