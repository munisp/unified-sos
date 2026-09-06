# mod-ppp-investment — PPP Pipeline Disclosure, QCBS & Concession Monitoring

**SOS module (g) PPP & Investment** (`docs/ppp-pipeline/module-state-fit.md`).
Offered to **TARIPA** (Taraba published pipeline + unsolicited-proposal guide
[LIVE], `docs/states/taraba.md`) and **NASIDA** (Nasarawa PPP law,
UKNIAF-supported manual, ₦212bn+ pipeline [LIVE], `docs/states/nasarawa.md`)
as near-zero-cost **entry gifts**; also serves Ogun PPP Office, Osun PPP-law
workflow, BIPC/BDIC, and Lagos concession monitoring.

## What it does

- **Pipeline registry** — projects through `PIPELINE → OBC → FBC →
  PROCUREMENT → AWARDED → IN_CONCESSION → CLOSED`, with a **public disclosure
  view** (`Project.public_view()`, published projects only) that structurally
  redacts internal notes and financial models.
- **Proposal intake** — solicited and **unsolicited proposals** (TARIPA
  published-guide flow): submission → compliance screening against the
  DOC-01…DOC-08 mandatory checklist (CAMA 2020, FIRS TCC, PENCOM/ITF/NSITF,
  NDPC license, ISO 27001/22301, performance bond) → QCBS evaluation → award.
- **QCBS scoring** — the 1,000-point model of
  `docs/procurement/evaluation-scorecards.md` [LIVE]:
  - Technical envelope max **700** across four categories (250/200/150/100),
    per-criterion maxima enforced by pydantic validation; **passing
    threshold 560 (80%)**.
  - Commercial envelope max **300**; concession-fee component uses
    lowest-bid normalization `S_comm = (lowest fee % / bid fee %) × weight`
    (weight 150 per the scorecard breakdown; 300 when the envelope is fee-only),
    plus CapEx commitment (80), step-down schedule (40), performance bond (30).
  - **Threshold gating**: the commercial envelope cannot be opened for a bid
    below 560; the service also rejects envelopes whose fee does not match the
    sealed bid.
- **OBC/FBC document sets** — per-stage document tracking with statuses.
- **Concession contracts** — milestones, monthly KPI records (one per period,
  append-only), and monthly **revenue-share reconciliation statements**:
  state share → TigerBeetle account **3001** (transfer code 101),
  concessionaire share → **2099** (code 103), per
  `ledger/chart-of-accounts.md` and ADR-002.
- **Step-down schedules** — `StepDownBand` guardrails (policy-pack
  configurable): as cumulative reconciled collections pass thresholds, the
  state revenue share steps up (fee tapering per the evaluation scorecard).
- **Procurement-integrity audit chain** — append-only, hash-chained
  `AuditEntry` records for every action; ICRC-compliant documentation from
  day one; `GET /audit/verify` recomputes the chain.

## Layout

- `app/models.py` — Project (+ `public_view()`), Proposal, TechnicalScore,
  CommercialScore, Evaluation, DocumentSet, ConcessionContract, Milestone,
  KPIRecord, StepDownBand, SettlementStatement, AuditEntry.
- `app/repo.py` — repository Protocol + in-memory impl; audit store has no
  update/delete path (Postgres schema-per-tenant + RLS in production).
- `app/service.py` — stage machine, screening gate, QCBS math + threshold
  gating, KPI/settlement lifecycle, step-down evaluation, audit chain.
- `app/main.py` — FastAPI HTTP surface (`/disclosure` is the public view).

## Run / test

```bash
cd services/mod-ppp-investment
pip install fastapi httpx pydantic pytest uvicorn
python3 -m pytest -q          # 20 tests
uvicorn app.main:app --port 8007
```

## Production notes

- Step-down bands, screening checklists and scoring weights belong in
  `config/states/<state>/` policy packs (80/20 rule, CONTRIBUTING.md); the
  in-code values mirror the published scorecard [LIVE].
- Public disclosure is served read-only via the API gateway; internal views
  require agency-scoped Keycloak roles.
- Settlement statements map 1:1 to TigerBeetle two-phase transfers; no SQL
  balance mutation (ADR-002).
