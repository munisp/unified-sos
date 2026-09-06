# Benue State Policy Pack

**Tier:** 3 — Shared Multi-Tenant (schema-isolated) · **Readiness:** 57.0 (Moderate) · **Wave:** 2

**Baseline [LIVE]:** Pop. 6.2M; IGR ₦24.1bn (~30th of 37); contractor/pension
arrears ₦99.68bn ≈ 5× annual IGR; >85% FAAC dependence. Fiscal distress means
zero tolerance for structures requiring state cash — vendor-financed only.
Anchor: food-basket produce, river transit, agro-logistics.

## Revenue split (`policy-pack.json`)

Head: `REV_AGRI_PRODUCE_CESS` — harmonized produce tax at control posts
(Aliade, Katsina-Ala, Otukpo), replacing illegal roadblocks. CRF 65% / BIRS
retention 15% / concessionaire escrow 15% INSTANT; LG pool 5% END_OF_MONTH.

Concession ceiling: **15%** (agrarian/extractive class, procurement guardrails).

## Enabled modules (`modules.yaml`)

| Module | Wave | Driver |
|---|---|---|
| `mod-rev-core` | 1 | B1 — unified IGR platform (BIRS + BDIC) |
| `mod-agri-waybill` | 1 | #6 — produce e-taxation & corridor waybills |
| `mod-gis-lands` | 2 | #7 — Makurdi & Gboko cadastre (BENGIS) |
| `mod-market` | 2 | B2 — Zaki Biam yam market (security-gated) |
| `mod-mining` | 3 | #8 — river sand-mining royalties |

## Key risks (from `docs/states/benue.md`)

- Largest single revenue pool but most stakeholder work — enter with
  four-state references (per entry sequencing).
- Sankera-axis security volatility gates market digitization (B2).
- Biometric civil-service clean-up is a Wave-0 prerequisite.
