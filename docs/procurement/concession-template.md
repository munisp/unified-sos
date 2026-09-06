# Standardized Six-State Technology-PPP Concession Term Sheet Template

**Document ref:** SOS-PPP-CONC-{{STATE_CODE}}-2026-V1 · Template for the
"standardized six-state concession template" action of the PPP pipeline
(docs/ppp-pipeline/). One executed term sheet per state, parameterized via the
`{{PLACEHOLDER}}` fields below and the per-state annex in Appendix A.

**Legal alignment:** ICRC Act 2005 · State PPP & Public Procurement Laws
(Nasarawa, Benue, Taraba, Ogun, Osun, Lagos) · NDPA 2023 · NITDA
local-content guidelines (see docs/procurement/README.md, "Legal Alignment").

> This template is a **term sheet**, not the executed concession agreement.
> It binds negotiation to the SOS procurement pack; the executed agreement
> must incorporate the mandatory clauses of
> [contract-clauses.md](contract-clauses.md) **verbatim**.

---

## 1. Parties & Recitals

- **The State:** The Government of **{{STATE_NAME}}**, acting through
  **{{LEAD_AGENCY}}** (per Appendix A), with the State PPP Unit /
  Bureau of Public Procurement (**{{PPP_UNIT}}**).
- **The Concessionaire:** **{{VENDOR_LEGAL_NAME}}** (CAC RC {{CAC_RC_NUMBER}}),
  selected under the QCBS process of
  [evaluation-scorecards.md](evaluation-scorecards.md) (1,000-point model:
  700 technical + 300 commercial).
- **Recitals:** (a) the State seeks vendor-financed digitization of revenue
  mobilization and public service delivery with **zero upfront state CapEx**;
  (b) the Concessionaire was evaluated under the SOS 8-lot packaging strategy
  ([lots.md](lots.md)); (c) both parties adopt the SOS architecture blueprint
  (docs/architecture/01-architecture-blueprint.md) as the technical baseline.

## 2. Scope of Concession

- **Modules in scope:** {{LOT_IDS}} per [lots.md](lots.md), mapping to work
  packages {{WP_IDS}} (docs/delivery/work-packages.md).
- **Hardware in scope:** {{HARDWARE_SCOPE}} (e.g., weighbridges, POS terminals,
  GNSS survey kits, edge sensors — per Procurement Pillar 1).
- **Out of scope:** any collection or custody of gross State revenues by the
  Concessionaire (Clause 22.2, contract-clauses.md).

## 3. Vendor-Financed, Zero-CapEx Structure

1. The Concessionaire funds **100%** of platform infrastructure, hardware,
   customization, and deployment. The State commits **no budgetary
   appropriation** and issues **no sovereign guarantee** beyond the statutory
   settlement mechanics of Clause 22.2.
2. Committed CapEx of **{{CAPEX_COMMITMENT_NGN}}** is a scored evaluation
   input (evaluation-scorecards.md, Commercial 300, 80 points) and becomes a
   contractual milestone schedule with an unconditional Tier-1 bank
   **performance bond of {{PERFORMANCE_BOND_PCT}}%**.
3. Capital recovery occurs **exclusively** through the declining revenue
   share in Section 4. No cost-recovery claims against the State outside the
   escrowed split are recognized.

## 4. Revenue-Share Mechanics

Grounded in lots.md "Commercial Guardrails" and Clause 22.2.

1. **Baseline measurement:** the IGR baseline is the audited average of the
   **{{BASELINE_YEARS}}** fiscal years preceding Effective Date
   (**{{BASELINE_IGR_NGN}}**), fixed by the Auditor-General. The
   Concessionaire earns **only on verified incremental IGR above baseline**.
2. **Programmatic settlement:** every transaction is split automatically at
   clearing into CRF, MDA retention, and the concessionaire escrow via
   TigerBeetle ledger execution (Clause 22.2). No private account ever holds
   un-split gross funds.
3. **Escrowed split:** the Concessionaire share accrues daily into a
   tripartite escrow at **{{ESCROW_BANK}}** (CBN-licensed Tier-1), released
   monthly against the reconciliation certificate in Section 9.
4. **Ceilings:** share of net incremental IGR shall not exceed
   **{{REV_SHARE_CEILING_PCT}}%** — ≤15% in agrarian/extractive states
   (Nasarawa, Benue, Taraba, Osun), ≤8% in high-volume industrial economies
   (Ogun, Lagos) (lots.md, Commercial Guardrails).
5. **Step-down schedule (mandatory, scored at 40 points):**

   | Incremental IGR tranche (per year) | Concessionaire share |
   |---|---|
   | First {{TRANCHE_1_NGN}} | {{SHARE_1_PCT}}% |
   | Next {{TRANCHE_2_NGN}} | {{SHARE_2_PCT}}% |
   | Excess | {{SHARE_3_PCT}}% |

   (Reference structure from lots.md: 15% on first ₦5bn, 10% on next ₦10bn,
   6% on excess.)
6. **IRR cap:** the Concessionaire's project IRR, measured on cumulative
   escrow receipts against audited invested capital, is capped at **22%**.
   Receipts above the cap **step down automatically** to {{IRR_CAP_SHARE_PCT}}%
   for the remainder of the tenor.

## 5. Tenor & BOT Phases

- **Model:** Build-Operate-Transfer (BOT). **Tenor:** **{{TENOR_YEARS}}**
  years (minimum 5, maximum 7) from Effective Date, structured per the
  24-month handover plan in
  [acceptance-framework.md](acceptance-framework.md):
  - **Phase 1 (M1–6):** 100% vendor-led; state personnel embedded.
  - **Phase 2 (M7–12):** 70% vendor / 30% state; state runs L1/L2 + ledger reconciliations.
  - **Phase 3 (M13–18):** 30% vendor / 70% state; state runs K8s, GitOps, Keycloak realms.
  - **Phase 4 (M19–24 and onward):** 100% state-autonomous operation; vendor as L3 support only.
- **Handover triggers:** (a) expiry of tenor; (b) IRR cap reached and
  {{IRR_EARLY_EXIT_MONTHS}} consecutive months at the stepped-down share;
  (c) termination for material breach. Each trigger activates Section 11.

## 6. Mandatory Clauses (incorporated verbatim)

From [contract-clauses.md](contract-clauses.md); non-negotiable:

1. **Clause 14.1 — Sovereign Data Ownership & NDPA 2023 Localization:**
   the State is sole owner of all data; the Concessionaire is a Data
   Processor under NDPA 2023; no data leaves Nigerian territory without
   Attorney-General + NDPC written authorization.
2. **Clause 16.3 — Open-Source Licensing & IP Escrow:** all bespoke software
   licensed Apache-2.0/MIT to the State; proprietary background technology
   deposited in tripartite Sovereign Software Escrow with release triggers
   (bankruptcy, 60-day SLA breach, abandonment).
3. **Clause 19.4 — Anti-Vendor Lock-In & Open APIs:** full data extraction in
   open formats (SQL dumps, GeoParquet, Delta Lake Parquet, GeoJSON, CSV)
   without vendor fee; all services expose OpenAPI 3.1-documented APIs.
4. **Clause 22.2 — Programmatic Statutory Revenue Settlement:** all revenues
   clear directly into the State CRF / gazetted TSA accounts; concessionaire
   share distributed only by automated TigerBeetle execution.

## 7. SLA & Service-Credit Schedule

The SLA/SLO regime of [sla-slo.md](sla-slo.md) is incorporated by reference:
availability tiers per component (ledger 99.999%, gateway 99.99%, GIS 99.95%,
lakehouse 99.90%), Sev-1 to Sev-4 MTTA/MTTR with service credits of 5% / 2% /
0.5% of the monthly concession fee, and CVSS-based vulnerability remediation
SLAs with per-day penalties. Service credits are deducted at source from the
monthly escrow release.

## 8. Institutionalization Clause (Lafia-Declaration style)

1. **Gazetting:** the executed concession, the revenue-share schedule, and
   the programmatic split rules shall be **gazetted** as a subsidiary
   instrument under {{STATE_PPP_LAW}} within {{GAZETTE_DAYS}} days of
   execution.
2. **Assembly visibility:** the State Executive shall lay the term sheet and
   annual concession performance report before the **{{STATE_HOUSE_OF_ASSEMBLY}}**;
   quarterly escrow reconciliation certificates are published on the State
   open-data portal.
3. **Statutory anchoring:** revenue-split rules are seeded as gazetted policy
   packs in the platform control plane (config/states/{{STATE_CODE}}/), so
   the split survives personnel, vendor, and administration changes.

## 9. Audit Mechanics (Real-Time Dual Auditability)

Per contract-clauses.md, "Real-Time Dual Auditability":

- **Auditor-General:** dedicated real-time **read-only replicas** of the
  TigerBeetle ledger and Delta Lake storage with cryptographic proof
  verification; unrestricted query access.
- **Accountant-General:** real-time **TSA verification** — visibility into
  all bank clearing settlements, confirming gross funds credit the State TSA
  instantly upon collection.
- **Monthly reconciliation certificate** jointly signed by the State IRS,
  Accountant-General, and Concessionaire is the sole instrument that
  releases the escrowed share (RACI: contract-clauses.md, "Monthly
  revenue-share reconciliation").

## 10. Successor-Administration Continuity Clause

This Agreement binds and benefits successors-in-office. A change of
Governor, lead-agency leadership, or ruling party **does not** terminate,
suspend, or renegotiate this Agreement; the gazetted instrument and the
programmatic split rules continue in force. Any amendment requires the same
process as execution: PPP-unit certification, gazetting, and House of
Assembly visibility (Section 8). Step-in rights of the Escrow Agent and the
Auditor-General survive any change of administration.

## 11. Exit, Handover & Termination

1. **Orderly BOT handover:** per acceptance-framework.md Phase 4 — source
   code, container images, runbooks, and all credentials transfer to the
   State; the Concessionaire continues as L3 support for
   {{L3_SUPPORT_MONTHS}} months post-handover.
2. **Escrow release on exit:** the Sovereign Software Escrow (Clause 16.3)
   releases automatically on vendor bankruptcy, 60-day material SLA breach,
   or failure to support.
3. **Data handover:** full export in open formats per Clause 19.4 within
   {{EXIT_EXPORT_DAYS}} days of any exit trigger, verified by the
   Auditor-General.
4. **Termination compensation:** limited strictly to undepreciated audited
   CapEx not yet recovered through the revenue share — no lost-profit claims.

## 12. Dispute Resolution

1. **Tier 1 — Technical panel:** joint State/Concessionaire panel chaired by
   the State CIO/CDO; 21 days.
2. **Tier 2 — Mediation:** under the auspices of {{PPP_UNIT}} / ICRC dispute
   mechanisms where applicable; 30 days.
3. **Tier 3 — Arbitration:** seated in {{ARBITRATION_SEAT}}, under the
   Arbitration and Mediation Act 2023; governing law is the law of
   {{STATE_NAME}} and the Federal Republic of Nigeria.

---

## Appendix A — Per-State Annex

| State | Lead agency | PPP law path | Rev-share ceiling | Tenancy tier |
|---|---|---|---|---|
| Lagos | {{LAGOS_LEAD_AGENCY — e.g., Lagos Internal Revenue Service / Office of PPP}} | {{LAGOS_PPP_LAW — Lagos State PPP Law}} | ≤8% (high-volume industrial) | Tier 1 — dedicated sovereign deployment |
| Ogun | {{OGUN_LEAD_AGENCY — e.g., Ogun State IRS}} | {{OGUN_PPP_LAW — Ogun State PPP law}} | ≤8% (high-volume industrial) | Tier 2 — hybrid (dedicated OLTP + shared lakehouse) |
| Nasarawa | {{NASARAWA_LEAD_AGENCY — NASIDA per state PPP law}} | {{NASARAWA_PPP_LAW — Nasarawa State PPP law (NASIDA, UKNIAF-supported manual)}} | ≤15% (agrarian/extractive) | Tier 3 — shared multi-tenant |
| Benue | {{BENUE_LEAD_AGENCY — Benue State IRS}} | {{BENUE_PPP_LAW — Benue State PPP/Public Procurement law}} | ≤15% (agrarian/extractive) | Tier 3 — shared multi-tenant |
| Osun | {{OSUN_LEAD_AGENCY — Osun State IRS}} | {{OSUN_PPP_LAW — Osun State PPP law}} | ≤15% (agrarian/extractive) | Tier 3 — shared multi-tenant (proof state for deepening tier) |
| Taraba | {{TARABA_LEAD_AGENCY — Taraba State IRS}} | {{TARABA_PPP_LAW — Taraba State PPP law}} | ≤15% (agrarian/extractive) | Tier 3 — shared multi-tenant with offline-first edge |

## Appendix B — Execution Checklist

- [ ] QCBS evaluation complete per evaluation-scorecards.md (≥ pass mark)
- [ ] Performance bond ({{PERFORMANCE_BOND_PCT}}%) lodged with Tier-1 bank
- [ ] Sovereign Software Escrow agreement executed (Clause 16.3)
- [ ] Gazetting instrument prepared (Section 8)
- [ ] Auditor-General / Accountant-General replica access provisioned (Section 9)
- [ ] Policy pack seeded in config/states/{{STATE_CODE}}/ and validated via `make validate`
- [ ] Acceptance gates mapped per acceptance-framework.md 5-stage lifecycle
