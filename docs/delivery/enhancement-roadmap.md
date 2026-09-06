# SOS Platform — Enhancement & Innovation Roadmap

## 1. PPP configurable profit-sharing — how it works today

The profit-sharing scheme is **not hardcoded** — it is a three-layer configurable mechanism:

1. **Gazetted split policies (config, not code).** Each revenue line carries a declarative split policy (see `contracts/policy-packs/examples/ogun-luc-2026.json` and the per-state policy packs in `config/states/<state>/policy-pack.json`). A policy defines the parties and their basis-point shares — e.g., state TSA / concessionaire escrow / MDA / LG — per revenue code. Changing the profit split = publishing a new gazetted policy version, never a code deploy. `sosctl policy` manages this.
2. **Atomic execution at payment time.** `ledger/splits` (TigerBeetle 128-bit double-entry) executes the split **inside the same linked transfer chain** as the payment — the concessionaire's fee never touches the state account first. Overdraft-safe, idempotent, replay-proof. This is what makes the **zero-capex concession model** enforceable across all six states.
3. **Bid-to-contract linkage.** `mod-ppp-investment` scores bids with `S_comm = (lowest acceptable concession fee % / bid fee %) × weight` and records the winning `concession_fee_bps`; that figure flows into the gazetted split policy. Escrow performance (step-down, performance bond, CapEx tranches) is tracked on the ledger with hash-chained procurement audit, publicly verifiable via `mod-transparency` (`/procurement/audit/verify`) and escrow statements.

**Result:** a state's PPP profit-sharing = a versioned JSON policy, executed atomically, audited publicly, changeable by gazette without vendor involvement.

## 2. Mobile app & UI/UX status (honest)

**Before this roadmap:** the platform was 100% backend — APIs, USSD/IVR channels, no consumer UI. USSD/IVR served feature phones; smartphone users had nothing.

**Now closed (this stage):** `apps/citizen-pwa/` — an installable React+TypeScript PWA: service catalog across all 12 domains, service-request flows with an offline submission queue, request tracking, payment initiation, C-of-O/deed verification, and a live transparency dashboard consuming the public audit feeds. English with i18n structure for Yorùbá/Hausa/Igbo; USSD shortcode parity shown for every flow so no citizen needs a smartphone.

**Remaining polish track:** native-shell wrappers (Capacitor) for app-store presence, push notifications via FCM, biometric app unlock bound to the KYC liveness engine, and MDA staff console UI (back-office dashboards today are API + Grafana only).

## 3. CRM & ERP integration — yes, needed; here's the shape

**CRM (citizen relationship):** needed for case management beyond one-shot requests — complaints, appeals, field-officer assignments. Recommendation: a thin `mod-crm` case-management module rather than embedding a third-party CRM (sovereignty + NDPA); sync outward to state call centres via the event backbone.

**ERP:** states run finance on assorted systems (some none). Recommendation: don't embed an ERP — publish a **finance-integration adapter pack**: IFMIS/GIFMIS export (COA-mapped journals from TigerBeetle), budget-performance feeds to the Planning Ministry, and SAP/Oracle/QuickBooks connectors behind the existing fail-closed adapter idiom for concessionaire-side accounting.

## 4. Outstanding gaps closed in this stage

| Gap | Resolution |
|---|---|
| No consumer UI / mobile app | `apps/citizen-pwa` PWA (offline-first, installable, transparency built in) |
| No CRM/case management | `mod-crm` recommended module (spec below, Innovation #3) |
| No ERP/finance integration | Finance adapter pack: IFMIS/GIFMIS journal export from the ledger |
| ML training seam (Ray/MLflow) | Innovation #5 — model registry + deterministic retraining pipeline |
| MDA staff console | Innovation #2 — back-office PWA consuming the same APIs |

## 5. Twenty innovations across the platform

**Citizen experience**
1. **AI Multilingual Service Assistant** — LLM-guided service discovery in EN/YO/HA/IG over the catalog API; voice-first for low-literacy users, grounded strictly in catalog data.
2. **MDA Staff Console PWA** — role-based back-office (officer queues, approvals, field collections) with the same offline-first edge sync as the daemon.
3. **mod-crm Case Management** — complaints/appeals/FOI requests as first-class workflows with SLA timers surfaced in the observability stack.
4. **Proactive Entitlement Engine** — from KYC attributes + policy packs, notify citizens of benefits/relief they qualify for (opt-in, NDPA consent-scoped).

**Revenue & finance**
5. **Model Registry + Retraining Pipeline** — Ray/MLflow seam fulfilled: versioned AVM/delinquency models, champion-challenger promotion gates, drift alerts wired to the existing alert rules.
6. **Revenue Forecasting Service** — lakehouse time-series models per state/sector feeding budget-performance dashboards; published to transparency for credibility.
7. **Dynamic Split Simulator** — "what-if" sandbox for gazetted split policies: simulate a policy version against 90 days of ledger history before gazettement.
8. **Agent Float Financing Ledger** — micro-loans to POS/field agents collateralized by their collection history; ledger-native, auto-repaid from split shares.

**Trust & integrity**
9. **Public Revenue Receipts (verify-any-payment)** — every payment issues a signed QR receipt; anyone scans to verify against the hash-chained ledger audit.
10. **Whistleblower Integrity Channel** — anonymous, end-to-end-encrypted leak reporting feeding police-cad/audit with legal-hold semantics.
11. **Procurement Twin** — every PPP project publishes milestone oracle data (IoT/satellite verification) so escrow releases are evidence-triggered, not paper-triggered.
12. **Cross-State Leakage Analytics** — federated lakehouse queries detecting waybill/ticket anomalies across state borders (Ogun–Lagos–Benue corridor first).

**Geospatial & land**
13. **Valuation Map Tiles API** — public map tiles of LUC bands/parcel status (redacted), so citizens see land charges before purchase; fraud deterrent.
14. **Change-Detection Alerts to Cadastre** — Sedona NDVI/building-change jobs auto-open cadastre review tasks (unpermitted construction discovery).
15. **3D Cadastre Layer** — volumetric parcels for high-rise Lagos; PostGIS 3D + GeoLibre visualization; aligned with the 3D LUC AI roadmap.

**Platform & operations**
16. **State Digital-Twin Dashboard** — per-state live operations twin: collections/minute, field-agent map, queue depths, gate status; born from existing Prometheus/Grafana + transparency feeds.
17. **Policy-as-Code Marketplace** — versioned, attestable policy packs shareable between states (Osun's market digitization policy reused by Benue in one import).
18. **Offline-First Everything Audit** — extend the edge outbox pattern to MDA offices with intermittent connectivity (hospital billing, school payments), not just field POS.
19. **Green-Ops Scheduler** — KEDA/Kubecost-driven workload shifting to solar-window hours for edge/kiosk charging cycles; cost + carbon reporting per state tier.
20. **Sovereign LLM Gateway** — on-platform LLM proxy (PII-redacting, hash-audited) so future AI features never leak citizen data to external APIs; NDPA-compliant by construction.

## 6. Prioritization

| Horizon | Items |
|---|---|
| Now (done/this stage) | Citizen PWA, transparency dashboards in-app |
| Next quarter | #1 AI assistant, #2 staff console, #3 CRM, #7 split simulator, #9 verify-any-payment |
| 6–12 months | #5 model registry, #6 forecasting, #11 procurement twin, #12 cross-state analytics, #13 valuation tiles |
| 12–24 months | #8 agent float, #15 3D cadastre, #16 digital twin, #20 sovereign LLM gateway |
