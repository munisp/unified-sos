# Osun State Policy Pack

**Tier:** 3 — Shared Multi-Tenant (schema-isolated) · **Readiness:** 71.0 (High) · **Wave:** 1

**Baseline [LIVE]:** Pop. 4.7M; IGR grew ₦24.5bn (2022) → ₦54.77bn (2024),
+97.3% via digitization without raising taxes; domestic debt cut ~47%.
Anchor: artisanal gold (Ilesha belt/Segilola), cocoa, heritage tourism. Osun is
the proof state for the deepening tier.

## Revenue split (`policy-pack.json`)

Head: `REV_MINING_ENV_LEVY` — state-competent mineral verification dues and
environmental levies (federal royalties are pass-through only, account 5001 is
never split). CRF 60% / Osun Mining Corporation retention 15% / concessionaire
escrow 15% INSTANT; LG pool 5% + revived Security Trust Fund (Amotekun) 5%
END_OF_MONTH.

Concession ceiling: **15%** (agrarian/extractive class, procurement guardrails).

## Enabled modules (`modules.yaml`)

| Module | Wave | Driver |
|---|---|---|
| `mod-rev-core` | 1 | S1 — OIRS deepening + market/transport e-ticketing |
| `mod-mining` | 1 | #21/S2 — gold traceability & artisanal formalization |
| `mod-agri-waybill` | 1 | #22 — cocoa supply chain & EUDR traceability |
| `mod-market` | 2 | #23 — Osogbo central market & stall titling |
| `mod-education` | 2 | #25 — tertiary consolidated billing |

## Key risks (from `docs/states/osun.md`)

- LG allocations crisis (~₦130bn withheld since Feb 2025) — structure at state level.
- 2026 election risk — front-load signatures into Q4 2026.
- Federal royalty constraint — levies/buying-centres/waybills only.
