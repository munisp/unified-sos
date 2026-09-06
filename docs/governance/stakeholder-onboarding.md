# Stakeholder Onboarding, Verification, Credentialing, Suspension & Audit Model

**Stage 4.1 deliverable** · Branch `feat/feature-inventory` · Date 2026-09-06
Companion to [`docs/delivery/feature-inventory.md`](../delivery/feature-inventory.md).

This document defines how each SOS stakeholder class is **onboarded, verified (KYC/KYB),
credentialed, suspended, and audited** across the six state tenants (lagos, ogun, osun,
benue, nasarawa, taraba). Status values: `IMPLEMENTED` / `PARTIAL` / `ADAPTER-SEAM` / `GAP`
(same vocabulary as the feature inventory).

## 0. Cross-cutting identity & trust fabric

| Capability | Mechanism | Implementation | Status |
|---|---|---|---|
| Authentication | Keycloak multi-realm OIDC (one realm `sos-<state>` per tenant; PKCE; MFA for admin roles; FIDO2 for revenue approvers) | `deploy/keycloak/realm-sos-dev.json`, ADR-006, WP-02 | PARTIAL (dev realm only; per-state realms pending) |
| Citizen identity anchor | NIN captured once → immediately reduced to SHA-256 (`hash_nin`); no read path returns raw NIN | `services/mod-identity`, `services/mod-citizen-portal` (`WalletRead.masked_nin`) | IMPLEMENTED (hash-at-rest); NIMC live verification ADAPTER-SEAM |
| Corporate identity anchor | CAC registration number + FIRS TCC (+ PENCOM/ITF/NSITF, NDPC licence, ISO 27001/22301, performance bond for PPP vendors) | DOC-01…08 checklist in `services/mod-ppp-investment` | PARTIAL (checklist IMPLEMENTED; registry verification ADAPTER-SEAM → Stage 4.3 KYB) |
| Consent & data rights | NDPA 2023 purpose-scoped, expiring, revocable consent per (resident, consumer, product); revocation blocks all future calls; data-minimized verification responses (boolean + refs only) | `services/mod-identity/app/service.py` + tests asserting no PII leakage | IMPLEMENTED (reference) |
| Authorization | Permify ReBAC mapping (resident / product / consumer entities); JWT tenant claims enforced by Postgres RLS (`app.current_state_tenant`) | mod-identity README mapping; `db/migrations/*` RLS | ADAPTER-SEAM (Permify) / IMPLEMENTED (RLS in migrations) |
| KYC/KYB engine | Document OCR (PaddleOCR), Docling document understanding, VLM adjudication seam, liveness challenge + anti-spoof, KYB registry verification, risk scoring, human review queue | **Planned** `services/mod-kyc-kyb/`, document-AI adapters (Stage 4.2/4.3); raw documents/biometrics stored as object references/hashes only, tenant-scoped, hash-audited | **GAP** |
| Audit | Hash-chained append-only audit (`GET /audit/verify`) in mod-identity & mod-ppp-investment; control-plane audit feed; OpenSearch immutable archive (7-yr) | In-module audit IMPLEMENTED (reference); OpenSearch sink ADAPTER-SEAM | PARTIAL |
| Suspension | Tenant lifecycle `active/suspended` (control-plane); consent revocation (mod-identity); permit `SUSPENDED` (mod-environment); provenance `SEIZED` (mod-forestry); dispute freeze (mod-market) | Per-module state machines | PARTIAL (no unified credential-suspension API until mod-kyc-kyb) |

**Sovereignty & NDPA constraints (binding for all flows below):** raw identity documents and
biometrics are never stored inline; only object references + SHA-256 hashes. Verification
results are minimized (attestation + reference IDs). All access is tenant-scoped and
hash-audited. Vendor acts solely as Data Processor; in-country residency mandatory.

---

## 1. Citizens / residents

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Create identity wallet via citizen portal; smartcard issuance fee settled automatically | `POST /citizen/v1/wallets` (mod-citizen-portal) | IMPLEMENTED (reference) |
| Verify (KYC) | NIN accepted once at wallet creation → SHA-256; production: NIMC API verification before wallet activation; Stage 4.3 adds document OCR + liveness for remote proofing (IAL2-style) | mod-citizen-portal seam; mod-kyc-kyb (planned) | PARTIAL → GAP for remote proofing |
| Credential | Wallet ID + Keycloak OIDC SSO session (`POST /citizen/v1/sso/sessions`); masked NIN on all reads | mod-citizen-portal + Keycloak | PARTIAL |
| Use | Per-state service catalog, multi-MDA service requests (STANDARD/EXPEDITED), e-petitions with public reference IDs | mod-citizen-portal | IMPLEMENTED (reference) |
| Suspend | Wallet suspension on fraud signal or consent withdrawal; SSO session revocation via realm | Keycloak + wallet state | PARTIAL (fraud-signal automation GAP pending risk scoring) |
| Audit | Every wallet/service/petition action audit-logged; consent grants/revocations hash-chained | mod-identity audit chain | IMPLEMENTED (reference) |

## 2. Civil servants

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | HR/payroll record import into civil-servant registry (migration `0004`); Keycloak realm account with MDA role | mod-citizen-portal (civil servants), WP-02 MDA RBAC | PARTIAL |
| Verify (KYC) | Biometric enrollment + re-verification notices driven by durable Temporal workflow (`payroll-cleanup` queue); duplicate biometric/salary-account hash detection | `PayrollAudit` rules IMPLEMENTED; capture devices + Temporal binding | PARTIAL (rules) / ADAPTER-SEAM (Temporal) / GAP (biometric capture) |
| Credential | MDA-scoped Keycloak credential; FIDO2 required for revenue-approval officers | WP-02 | ADAPTER-SEAM |
| Suspend | Payroll hold via audit finding; account disable via realm; recoverable savings computed in kobo | PayrollAudit outcomes | PARTIAL |
| Audit | Deterministic audit rules (unverified biometric, duplicates, inactive-still-paid); every audit run recorded; ghost-worker savings KPI ₦500m+ | mod-citizen-portal | IMPLEMENTED (reference) |

## 3. MDA staff (service officers)

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | MDA sub-tenant provisioning via control plane; staff accounts in state realm with hierarchical MDA RBAC | control-plane + WP-02 EP-IAM-03 | PARTIAL |
| Verify | Civil-servant verification (above) + role vetting by MDA admin | WP-02 | ADAPTER-SEAM |
| Credential | OIDC + MFA; MDA boundary claims in JWT; least-privilege per module (surveyor, town planning, AG, governor signatory roles in e-C-of-O) | Keycloak; mod-gis-lands titling roles | PARTIAL |
| Suspend | Realm account disable; role revocation; tenant-level suspension cascades via control-plane `suspended` state | control-plane + Keycloak | PARTIAL |
| Audit | Control-plane audit feed; per-module audit (e.g., procurement chain, consent denials) | control-plane, mod-identity, mod-ppp-investment | PARTIAL |

## 4. POS / field agents

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Agent enrollment by MDA/concessionaire; device provisioning (ruggedized Android POS, solar kiosks) | edge hardware profiles | ADAPTER-SEAM (device procurement) |
| Verify (KYC) | Agent NIN + biometric verification + device binding — **planned in mod-kyc-kyb** (document + liveness + risk score); today only device key issuance exists | mod-kyc-kyb (Stage 4.3); `DeviceSigner` key issuance | GAP (agent KYC) / PARTIAL (device identity) |
| Credential | Ed25519 device signing key (stand-in for hardware SE); `(device_id, sequence)` monotonic outbox identity; mTLS client certs on sync | `edge/edge-daemon` crypto/outbox/sync | IMPLEMENTED (reference); SE binding ADAPTER-SEAM |
| Use | Offline-first signed ticket issuance (≥5,000 cached), batch sync to APISIX via mTLS; mod-market ingestion verifies signatures + dedupe | edge daemon + mod-market (wire-compat test) | IMPLEMENTED (reference) |
| Suspend | Device key revocation + gateway rejection of revoked `device_id`; agent account disable | Gateway denylist | ADAPTER-SEAM (revocation list distribution) |
| Audit | Signed, sequenced, hash-verifiable ticket batches; append-only dispute trail | mod-market + edge outbox | IMPLEMENTED (reference) |

## 5. Market traders

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Stall cadastre assignment → concession lease → trader registry entry (Osogbo Central 35k-trader scale) | mod-market registry | IMPLEMENTED (reference); lease UI (Form.io) ADAPTER-SEAM |
| Verify (KYC) | Tiered informal-sector KYC: NIN where available; document OCR + risk scoring via mod-kyc-kyb (planned); agent-assisted enrollment at market kiosks | mod-kyc-kyb (Stage 4.3) | GAP |
| Credential | Trader ID bound to stall; USSD/POS payment channels; STIN linkage for daily stallage (transfer code 130) | mod-market + mod-rev-core STIN seam | PARTIAL |
| Suspend | Stall lease suspension; dispute workflow freezes contested tickets (append-only arbitration trail) | mod-market dispute workflow | IMPLEMENTED (reference) |
| Audit | Double-charge prevention; signed offline tickets; unique stall+day ticket constraint | mod-market | IMPLEMENTED (reference) |

## 6. Miners

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Licensed operators: site registration → mineral e-permit; artisanal miners: biometric registry (named module scope) | mod-mining site/permit registry | PARTIAL (licensed) / GAP (artisanal biometric) |
| Verify | **KYB**: CAC + mining licence + federal/state MoU standing; **KYC**: artisanal biometric capture + liveness via mod-kyc-kyb (planned) | mod-ppp-investment checklist pattern; mod-kyc-kyb | ADAPTER-SEAM (KYB) / GAP (artisanal KYC) |
| Credential | Permit ID; RFID truck manifests; checkpoint scanner credentials | mod-mining + seams | PARTIAL |
| Suspend | Permit revocation; consignment hold at checkpoint; royalty/levy split enforcement rejects federal-share claims by construction | mod-mining (`RoyaltyConstraintViolation`) | IMPLEMENTED (constraint) / PARTIAL (revocation ops) |
| Audit | Consignment lifecycle events on `ng.sos.mining.*`; levy assessments auditable vs state fee schedules (< 1% variance acceptance) | mod-mining + AsyncAPI | PARTIAL (bus ADAPTER-SEAM) |

## 7. Transporters / haulage operators

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Operator registration → e-manifest issuance; union membership capture for commission splits | mod-transport-wim manifests; mod-mobility-switch operators | PARTIAL |
| Verify | **KYB** for haulage firms (CAC); driver manifest KYC; vehicle/plate identity via ANPR↔WIM correlation (±30 s) | mod-mobility-switch (manifests seam); mod-transport-wim correlation | PARTIAL (correlation IMPLEMENTED); KYB ADAPTER-SEAM |
| Credential | E-manifest ID verifiable at checkpoints (< 5 s); agri e-waybill QR (`SOSWB1.*` HMAC) for produce haulage | mod-transport-wim `/manifests/{id}/verify`; mod-agri-waybill | IMPLEMENTED (reference) |
| Suspend | Manifest/waybill invalidation; overload fine enforcement (code 120); corridor bans | mod-transport-wim, mod-agri-waybill | PARTIAL |
| Audit | Every WIM reading published on shared envelope; fine assessments ledger-posted | mod-transport-wim | PARTIAL (bus ADAPTER-SEAM) |

## 8. Facilities (industrial, health, education)

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Facility registration per domain: industrial (environment), hospital (health billing accounts), institution (education billing) | mod-environment, mod-health, mod-education | IMPLEMENTED (reference) |
| Verify | **KYB**: CAC + sector licence + EIA status (industrial); SHIA/NHIS provider standing (health); institution accreditation (education) | Checklist pattern; registry adapters planned | ADAPTER-SEAM |
| Credential | Facility ID + tenant-scoped API credentials; IoT ingestion credentials for telemetry | mod-environment telemetry ingest | PARTIAL |
| Suspend | Permit lifecycle `SUSPENDED/EXPIRED`; claim rejection; invoice lock (education 423 on outstanding fees) | mod-environment, mod-health, mod-education | IMPLEMENTED (reference) |
| Audit | Compliance rate per facility; violation incidents with deterministic fines; 4-h deforestation/enforcement SLA; claims lifecycle trail | mod-environment | IMPLEMENTED (reference) |

## 9. Vendors / concessionaires (incl. verification-API consumers)

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | PPP pipeline: solicited/unsolicited proposal intake (TARIPA guide flow) → OBC/FBC stages | mod-ppp-investment pipeline registry | IMPLEMENTED (reference) |
| Verify (KYB) | Mandatory DOC-01…08: CAMA 2020, FIRS TCC, PENCOM, ITF, NSITF, NDPC licence, ISO 27001/22301, performance bond; QCBS 1,000-pt evaluation (560/700 technical threshold; commercial envelope sealed until pass) | mod-ppp-investment screening + QCBS | IMPLEMENTED (scoring/checklist); registry verification ADAPTER-SEAM (Stage 4.3 KYB) |
| Credential | Awarded concession contract; API consumers get product subscriptions (ADDRESS_VERIFICATION, RESIDENCY_ATTESTATION, KYC_ADJUNCT) gated by per-resident consent | mod-ppp-investment; mod-identity | IMPLEMENTED (reference) |
| Suspend | Contract milestone hold; consent revocation blocks consumer calls immediately (HTTP 403, denial audit-logged); tenant suspension via `sosctl tenant suspend` | mod-identity revocation; control-plane | IMPLEMENTED (reference) |
| Audit | Hash-chained procurement-integrity audit (`GET /audit/verify`); monthly revenue-share reconciliation (state 3001 / concessionaire 2099, codes 101/103); step-down schedules | mod-ppp-investment + ledger | IMPLEMENTED (reference) |

## 10. Auditors

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Auditor role in state realm (or ICRC/AG federation); read-only scopes | Keycloak RBAC | ADAPTER-SEAM |
| Verify | FIDO2 + MFA mandatory; organization attestation | WP-02 | ADAPTER-SEAM |
| Credential | Read-only audit scopes: `GET /audit/verify` (identity, PPP), control-plane audit feed, public trust-fund views | mod-identity, mod-ppp-investment, control-plane | PARTIAL |
| Suspend | Credential revocation via realm | Keycloak | PARTIAL |
| Audit | Auditors themselves are audit-logged (access to audit archives recorded); OpenSearch 7-yr immutable retention | OpenSearch seam | ADAPTER-SEAM |

## 11. Administrators (platform & state)

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | Platform operators provisioned manually (bootstrap); state admins via tenant provisioning (`sosctl tenant create`) | control-plane + sosctl | IMPLEMENTED (reference) |
| Verify | Corporate/employment vetting out-of-band; MFA + FIDO2 for privileged actions | WP-02 | ADAPTER-SEAM |
| Credential | Keycloak admin realm; GitOps-signed commits as action identity; zero data-plane PII access (PII guard middleware, HTTP 400) | control-plane `pii_guard.py` | IMPLEMENTED (guard) / PARTIAL (realm ops) |
| Suspend | `sosctl tenant suspend --state=<s> --reason=...`; account disable | sosctl + control-plane lifecycle | IMPLEMENTED (reference) |
| Audit | Every mutation = ArgoCD GitOps commit + control-plane audit event; OpenSearch archive | sosctl design rules; control-plane | PARTIAL |

## 12. Corporate entities (taxpayers, carbon-market actors, developers)

| Step | Flow | Mechanism / path | Status |
|---|---|---|---|
| Onboard | STIN issuance linked to CAC (corporate taxpayers); carbon project/credit registration; PPP proposal intake | mod-rev-core (STIN seam); mod-environment carbon registry; mod-ppp-investment | PARTIAL |
| Verify (KYB) | CAC registry + FIRS TCC verification; beneficial-ownership capture; risk scoring + review queue — **planned mod-kyc-kyb** | mod-kyc-kyb (Stage 4.3) | ADAPTER-SEAM → GAP (automated KYB) |
| Credential | STIN; carbon credit serials (unique per tenant); concession contracts | mod-rev-core; mod-environment; mod-ppp-investment | IMPLEMENTED (reference) |
| Suspend | Assessment/billing halt; credit freeze (no transfer while suspended); contract milestone hold | Per-module state machines | PARTIAL |
| Audit | Idempotent assessment trail; credit lifecycle (REGISTERED→ISSUED→transferred/retired) with brokerage settlement lines; hash-chained procurement audit | mod-rev-core, mod-environment, mod-ppp-investment | IMPLEMENTED (reference) |

---

## 13. Onboarding maturity summary

| Stakeholder | Onboarding | Verification (KYC/KYB) | Credentialing | Suspension | Audit |
|---|---|---|---|---|---|
| Citizens | IMPLEMENTED | PARTIAL (NIMC seam; liveness GAP) | PARTIAL | PARTIAL | IMPLEMENTED |
| Civil servants | PARTIAL | PARTIAL (biometric capture GAP) | ADAPTER-SEAM | PARTIAL | IMPLEMENTED |
| MDA staff | PARTIAL | ADAPTER-SEAM | PARTIAL | PARTIAL | PARTIAL |
| POS/field agents | ADAPTER-SEAM | GAP (Stage 4.3) | IMPLEMENTED (device) | ADAPTER-SEAM | IMPLEMENTED |
| Market traders | IMPLEMENTED | GAP (tiered KYC, Stage 4.3) | PARTIAL | IMPLEMENTED | IMPLEMENTED |
| Miners | PARTIAL | ADAPTER-SEAM (KYB) / GAP (artisanal KYC) | PARTIAL | PARTIAL | PARTIAL |
| Transporters | PARTIAL | PARTIAL | IMPLEMENTED (manifest/waybill) | PARTIAL | PARTIAL |
| Facilities | IMPLEMENTED | ADAPTER-SEAM | PARTIAL | IMPLEMENTED | IMPLEMENTED |
| Vendors/concessionaires | IMPLEMENTED | PARTIAL (checklist IMPLEMENTED, registry ADAPTER-SEAM) | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED |
| Auditors | ADAPTER-SEAM | ADAPTER-SEAM | PARTIAL | PARTIAL | ADAPTER-SEAM |
| Administrators | IMPLEMENTED | ADAPTER-SEAM | PARTIAL | IMPLEMENTED | PARTIAL |
| Corporate entities | PARTIAL | ADAPTER-SEAM → GAP (automated KYB, Stage 4.3) | IMPLEMENTED | PARTIAL | IMPLEMENTED |

**Critical path:** the Stage 4.3 `mod-kyc-kyb` capability (document OCR, Docling, VLM
adjudication seam, liveness, KYB registry verification, risk scoring, review queues) unblocks
the `GAP` cells above — most acutely POS-agent KYC, trader tiered KYC, artisanal-miner
biometrics, and automated corporate KYB — while preserving the sovereignty constraints
(object-reference document storage, minimized results, tenant-scoped hash-audited access).
