# Taraba State Policy Pack

**Tier:** 3 — Shared Multi-Tenant (schema-isolated, offline-first edge) · **Readiness:** 49.0 (Moderate) · **Wave:** 2/3

**Baseline [LIVE]:** Pop. 3.6M; IGR ₦17.46bn (2024, 34th of 37); >85% FAAC
dependence. Anchor: rosewood/forestry, Mambilla tea/coffee, Cameroon border
trade. **Proof point:** TSIRS doubled monthly IGR (₦800m → ₦1.6bn) after the
2024 reform and Aug 2025 automation — the program's most important live
demonstration.

## Revenue split (`policy-pack.json`)

Head: `REV_FORESTRY_TIMBER_ROYALTY` — stumpage royalties enforced via RFID
provenance tagging (untagged-log movement ban). CRF 60% / Forestry Commission
retention 15% / concessionaire escrow 15% INSTANT; LG pool 5% END_OF_MONTH.

Concession ceiling: **15%** (agrarian/extractive class, procurement guardrails).

## Enabled modules (`modules.yaml`)

| Module | Wave | Driver |
|---|---|---|
| `mod-gis-lands` | 1 | T1 — TAGIS recertification (first overall build) |
| `mod-rev-core` | 1 | T2 — TSIRS expansion (Revenue Core reference config) |
| `mod-forestry` | 1 | #11 — rosewood/timber provenance RFID |
| `mod-agri-waybill` | 2 | #12/#13 — Mambilla tea traceability & border trade |
| `mod-mining` | 3 | #15 — sapphire/barite asset tracking |

## Key risks (from `docs/states/taraba.md`)

- Requires edge hardening + solar power at remote corridors before full
  activation (offline-first POS per resilience doc).
- Bottom-five IGR base — anchor on the TSIRS doubling proof, not projections.
- Cross-border (Cameroon) interfaces need bilingual manifest handling.
