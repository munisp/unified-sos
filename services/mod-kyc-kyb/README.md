# mod-kyc-kyb — KYC/KYB, Document AI & Liveness

Sovereign, tenant-isolated KYC/KYB capability for SOS: evidence capture,
document AI (PaddleOCR / Docling / VLM adjudication), next-generation
active + passive liveness, registry verification (CAC / NIMC / sanctions
seams), deterministic risk scoring, case management, review queues, and a
hash-chained audit log.

Complements `mod-identity` (resident registry, boolean verification),
`mod-citizen-portal` (wallets, SSO), and `control-plane` (tenant policy).
It does **not** duplicate those modules.

## Stakeholder onboarding matrix

| Stakeholder | Flow | Required documents (default) | Liveness |
|---|---|---|---|
| Citizen (wallet) | KYC | NIN_SLIP | active |
| Resident | KYC | NIN_SLIP, PROOF_OF_ADDRESS | active |
| Field/POS agent | KYC | NATIONAL_ID, PROOF_OF_ADDRESS | active |
| Civil servant / MDA officer (employee) | KYC | NATIONAL_ID | active |
| Vendor contact | KYC | NATIONAL_ID | active |
| Market trader / miner / transporter / health / education user | KYC | per subject type | active |
| Vendor / concessionaire / corporate entity | KYB | CAC_CERTIFICATE, BENEFICIAL_OWNERSHIP_DECLARATION (+ POA, TAX_CLEARANCE, BOARD_RESOLUTION as required) | BO KYC cross-reference |
| Auditor | KYC | NATIONAL_ID | active |

## Flows (text form)

### KYC

```
create case -> attach document artifacts -> extraction pipeline
  (PaddleOCR -> Docling -> VLM adjudication -> consensus score + mismatch warnings)
-> issue liveness challenge -> submit liveness evidence (score)
-> submit case -> deterministic risk engine
  LOW      -> auto-approve (docs valid, liveness passed, no sanctions/mismatch)
  MEDIUM   -> IN_REVIEW
  HIGH     -> IN_REVIEW
  PROHIBITED -> reject (policy)   [sanctions hit forces PROHIBITED]
-> reviewer approve/reject (with reason) -> audit
```

### KYB

```
create case (legal name, RC, TIN, business type)
-> attach CAC certificate / proof of address / tax clearance /
   board resolution / beneficial-ownership declaration
-> registry verification (CAC seam + sanctions screen; responses hashed)
-> add directors / beneficial owners (total ownership > 100% rejected;
   optional KYC case cross-reference per owner)
-> submit -> deterministic risk scoring -> approve / review / reject
-> reviewer decision -> audit
```

## Adapter configuration

| Adapter | Env var | Notes |
|---|---|---|
| PaddleOCR | `PADDLEOCR_ENABLED=1` | Optional import of `paddleocr.PaddleOCR`; fails closed when missing. Parses surname, first name, document number, DOB, expiry, address, business name, RC number. |
| Docling | `DOCLING_ENABLED=1` | Optional import of `docling` DocumentConverter; layout/table-aware confidence; fails closed. |
| VLM | `VLM_ENABLED=1`, `VLM_ENDPOINT_URL` | OpenAI-compatible endpoint or injected local model callable; no hardcoded vendor. Compares VLM fields with OCR/Docling and surfaces `mismatch:<field>` warnings; rationale stored as hash only. |
| CAC | wire `CacRegistryAdapter(enabled=True, client=...)` | Seam only; fail-closed by default. |
| NIMC | wire `NimcAdapter(enabled=True, client=...)` | Seam only; fail-closed by default. |
| Sanctions | wire `SanctionsAdapter(enabled=True, client=...)` | Seam only; fail-closed by default. |

Registry responses are hashed (`response_hash`); raw registry payloads are
never stored or returned.

## Local/test vs production mode

- `KYC_KYB_MODE=local|test` (default `local`): deterministic simulated
  adapters (`SimulatedDocumentAIAdapter`, `SimulatedVLMAdapter`,
  `FixtureRegistryAdapter`) — no network, reproducible tests.
- Any other mode: production seams are wired fail-closed; each optional
  dependency must be explicitly enabled and configured. Unavailable
  adapters raise `AdapterUnavailableError` and the failure is audited.

## NDPA data-minimization posture

- Raw NIN, CAC documents, biometrics, images, videos, and OCR text are
  **never returned** by read APIs.
- The platform stores: object URI + SHA-256 hash + minimum extracted fields
  + confidence scores. Subject references, legal names, RC numbers, TINs,
  addresses, and beneficial-owner identities are stored as SHA-256 hashes
  with non-sensitive display labels.
- Liveness evidence carries scores and artifact hashes only — never media.

## Risk thresholds and reviewer policy

Deterministic signal weights (score 0–100, higher = riskier):

| Signal | Weight |
|---|---|
| MISSING_DOCUMENTS | 50 |
| EXTRACTION_MISMATCH / tamper | 30 |
| LIVENESS_NOT_PASSED | 45 |
| SANCTIONS_HIT | 100 (forces PROHIBITED) |
| REGISTRY_MISMATCH | 55 |
| REGISTRY_NOT_FOUND | 40 |
| REGISTRY_UNAVAILABLE | 35 |
| OWNERSHIP_UNVERIFIED | 20 |
| PEP_OWNER (KYB) | +25 |

Bands: LOW ≤ 25 (auto-approve if clean), MEDIUM ≤ 60 (IN_REVIEW),
HIGH ≤ 85 (IN_REVIEW), PROHIBITED > 85 (reject per policy).
Reviewers act on `/kyc|kyb/v1/review-queue` tasks with an explicit reason;
every decision and read is appended to the hash-chained audit log
(`/kyc-kyb/v1/audit/verify` validates chain integrity).

## API quickstart

```bash
uvicorn app.main:app --port 8090

# KYC
curl -X POST localhost:8090/kyc/v1/cases -H 'Content-Type: application/json' \
  -d '{"state_id":"lagos","subject_ref":"wallet-1","subject_type":"CITIZEN_WALLET"}'
# attach document -> extract -> liveness challenge -> evidence -> submit -> review

# KYB
curl -X POST localhost:8090/kyb/v1/cases -H 'Content-Type: application/json' \
  -d '{"state_id":"lagos","legal_name":"Acme Ltd","rc_number":"RC123","address":"1 Broad St"}'

curl localhost:8090/healthz
curl 'localhost:8090/kyc-kyb/v1/audit/verify'
```

## Development

```bash
python -m pytest -q          # 42 tests, no network
python -m compileall -q app tests
```
