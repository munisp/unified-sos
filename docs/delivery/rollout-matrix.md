# Cross-State Rollout Matrix & Phased Deployment Sequencing

## Strategic Rollout Imperative

A monolithic "big-bang" deployment across six states and 30 modules is a fatal delivery risk. SOS couples a **shared standardized core** (TigerBeetle, Mojaloop, Keycloak, Sedona) with **differentiated state-by-state phasing** aligned to fiscal baseline, institutional readiness, and the highest-yielding PPP opportunities.

## 5-Pillar State Readiness Framework (scores 0–100)

| Pillar | Criteria |
|---|---|
| 1. Institutional & Sponsor Alignment | Governor buy-in, BIR leadership stability, inter-ministerial task force, union openness |
| 2. Digital & Infrastructure Baseline | Data center/cloud, fiber backbone, 4G field coverage, existing GIS base maps |
| 3. Legal & Statutory Readiness | Consolidated Revenue Law, PPP Law (ICRC model), cadastral/LUC gazettes, enforcement orders |
| 4. Fiscal & Settlement Maturity | TSA enforcement, bank settlement APIs, auto-split escrow, BIR audit discipline |
| 5. Operational & Field Enforcement | Enforcement corps, revenue marshals, weighbridge infrastructure, biometric kiosks |

### Composite Readiness & Wave Placement

| State | Composite Score | Posture | Deployment Wave |
|---|---|---|---|
| Lagos | 93.0 | Mature | **Wave 0/1** |
| Ogun | 83.0 | Very High | **Wave 1** |
| Nasarawa | 73.0 | High | **Wave 1** |
| Osun | 71.0 | High | **Wave 1** |
| Benue | 57.0 | Moderate | **Wave 2** |
| Taraba | 49.0 | Moderate | **Wave 2/3** |

**Sequencing logic:** Lagos & Ogun's fiscal/digital maturity enables immediate complex integrations; Nasarawa's NAGIS infrastructure + FCT-border alignment is the fastest high-yield quick win; Osun's legal structures suit immediate cocoa/gold and market digitization; Benue needs Wave-0 biometric civil-service cleansing first; Taraba requires edge hardening, solar power at remote corridors, and TAGIS digitizing before full activation.

## Master 4-Wave Rollout (Months 1–24)

| Wave | Window | Focus & Modules | Participating States | Gate to Next Wave |
|---|---|---|---|---|
| **Wave 0 — Foundation** | M1–M3 | Core K8s, TigerBeetle hub, Mojaloop switch, Keycloak multi-realm | Lagos (transit pilot), Ogun (Sagamu-Ore WIM), Nasarawa (lithium weighbridge) | **Gate 0→1:** zero-downtime Keycloak realms; TigerBeetle 3-node >10k TPS; WAF operational; settlement bank API verified |
| **Wave 1 — Quick Wins** | M4–M8 | `mod-rev-core`, cadastral GIS & e-C-of-O, minerals & forestry provenance | Nasarawa (Karu titling), Osun (gold/cocoa EUDR), Ogun (emissions IoT), Lagos (transit & cadastre), Benue (produce e-tax), Taraba (forestry RFID) | **Gate 1→2:** ₦10bn cumulative cleared through TigerBeetle with 100% automated split reconciliation |
| **Wave 2 — Sector Expansion** | M9–M16 | Agribusiness e-tax & waybills, waterways, tertiary & hospital billing, ports | Benue (Makurdi cadastre), Lagos (high-density cadastre & waterways), Osun (tertiary billing), Ogun (corridor haulage) | **Gate 2→3:** Sedona processing >5M parcel boundaries; satellite change detection live; cross-state e-waybill exchange (Ogun–Lagos–Benue) |
| **Wave 3 — Full AI & Police** | M17–M24 | Cross-border trade, highland agro-traceability, Ray AI valuation, 112 CAD | Taraba (rosewood, Mambilla, border), Lagos & Ogun (multi-modal AI dispatch), all states (federated lakehouse) | **Gate 3:** 30 modules fully live; complete multi-state federation |

## Tenancy & Infrastructure Tiers

| Tier | States | Compute & Storage Baseline | Est. Cost/Month | Allocation |
|---|---|---|---|---|
| Tier 1 — Dedicated Sovereign | Lagos | Dedicated EKS: 16× c6i.4xlarge + 6× r6i.4xlarge, 15 TB NVMe; dedicated 3-node TigerBeetle | $28,000–$42,000 | 100% Lagos PPP concession budget |
| Tier 2 — Hybrid Industrial | Ogun | Dedicated DB/node pool: 8× c6i.2xlarge + 4× r6i.2xlarge, 5 TB NVMe + shared lakehouse | $9,500–$14,000 | 100% Ogun PPP concession budget |
| Tier 3 — Shared Multi-Tenant | Osun, Benue, Nasarawa, Taraba | Shared EKS: 12× c6i.4xlarge + 6× r6i.4xlarge, 10 TB NVMe (schema-isolated) | $1,800–$4,500 per state | Pro-rata Kubecost allocation by transaction volume |

## Zero-Capex Concession Structuring Gate

No state is provisioned without: (1) executed PPP Concession Agreement; (2) gazetted statutory revenue-split order escrowing concessionaire fees via TigerBeetle; (3) approved NDPA Data Protection Compliance Statement.
