# Nasarawa State Policy Pack

**Tier:** 3 — Shared Multi-Tenant (schema-isolated) · **Readiness:** 73.0 (High) · **Wave:** 1

**Baseline [LIVE]:** Pop. 2.9M; IGR ₦20.5bn (NBS; state DSA figure higher —
discrepancy flagged, baselines independently measured); 5% of IGR set aside to
de-risk private investment. Anchor: lithium/solid minerals + Karu FCT-border
sprawl. Strongest PPP institutional readiness of the original three: NASIDA
under state PPP law with a ₦212bn+ pipeline.

## Revenue split (`policy-pack.json`)

Head: `REV_MINING_HAULAGE_PERMIT` — state-competent haulage permits,
environmental fees and transit royalties on the lithium corridor (federal
royalties are pass-through only, account 5001 is never split). CRF 60% /
Mineral Resources Development Agency retention 15% / concessionaire escrow 15%
INSTANT; LG pool 5% + mining-corridor security fund 5% END_OF_MONTH.

Concession ceiling: **15%** (agrarian/extractive class, procurement guardrails).

## Enabled modules (`modules.yaml`)

| Module | Wave | Driver |
|---|---|---|
| `mod-mining` | 1 | #1/N1 — mining custody & lithium haulage e-waybill |
| `mod-gis-lands` | 1 | #2/N2 — Karu high-density land titling (NAGIS) |
| `mod-market` | 1 | N3/N4 — market & levy (cheapest, fastest, governor-visible) |
| `mod-rev-core` | 1 | N3/N5 — revenue core |
| `mod-health` | 2 | #5 — hospital billing & drug POS |
| `mod-agri-waybill` | 2 | #4 — warehouse receipts & transit |

## Key risks (from `docs/states/nasarawa.md`)

- IGR baseline discrepancy (NBS vs DSA) — measure independently.
- Federal–state mining cadastre interface — CDA enforcement via escrow only.
- Lithium urgency — capture flight before informal channels entrench.
