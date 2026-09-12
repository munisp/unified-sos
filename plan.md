# Plan — National Edition Expansion (36 States + FCT, 180 opportunities)

Source: uploaded National Edition blueprint (16 canonical modules) + PPP Opportunity Atlas (36 states) + readiness xlsx. Scope confirmed: new modules + ALL 30 new states + FCT policy packs.

## Stage 1 — 3 new module services (3 parallel coder subagents, strict ownership)
- `mod-agri-trace` (branch feat/mod-agri-trace): commodity aggregation, agro-hub warehouse receipts, crop traceability. Mojaloop/Temporal/PostGIS idioms; per spec: Benue yam, Osun cocoa, Taraba tea, Kebbi rice, Kano grains.
- `mod-border-transit` (feat/mod-border-transit): cross-border cargo RFID tracking, transit telematics, corridor clearance. Fail-closed RFID/telematics adapters + fixtures. Taraba/Borno/Katsina/Sokoto/Ogun/Cross River.
- `mod-waterways` (feat/mod-waterways): inland waterways ferry e-ticketing, sand-dredging volumetric monitoring (Sedona seam). Lagos/Bayelsa/Rivers/Benue/Delta/Kogi/Niger.
Same patterns: FastAPI+pydantic v2, TigerBeetle hook, fail-closed adapters, X-State-Tenant, /healthz + /metrics, hashchain audit, ≥20 tests each, Dockerfile, README (mod-police-cad style).

## Stage 2 — 30 new state + FCT policy packs (orchestrator, programmatic)
Mirror config/states/<state>/{policy-pack.json, modules.yaml, erp-coa.yaml?} schema from existing 6. Module enablement from atlas table 4 (priority modules per state) + module adoption matrix from national spec §3. Universal modules: rev-core, health, education, citizen-portal, kyc-kyb, transparency, erp-bridge. Tier assignment by IGR table (xlsx/table 0).

## Stage 3 — Integration wiring
KNOWN_MODULES, compose (ports 8018-8020), helm values+schema, prometheus jobs, openapi generator entries, AsyncAPI channels (warehouse_receipt_issued, transit_crossing_recorded, dredging_volume_alert etc.), registry regen, rollout matrix + docs updates (feature inventory, scorecard §12, state-opportunity-coverage national note).

## Stage 4 — Validation + push
Per-service pytest, validate_packs (37 packs), validate_infra, registry --check, gates. Commit + push w/ retries. Remind PAT revocation.


## Stage 10 — Whitelabel & per-state domains (in flight)
- WS-A feat/tenant-whitelabel: control-plane branding registry + domain mapping, 37 branding.json seeds, sosctl bundle domain manifest + `tenant branding` cmd, event ng.sos.tenant.branding_updated.
- WS-B feat/whitelabel-frontend: theming/branding in citizen-pwa + dispatch-console (CSS vars, title, favicon, monogram, locale default, demo fixtures).
- Integration: merge both, registry regen, openapi regen, validate, docs (inventory F-048, scorecard §13), push.


## Stage 11 — Consolidated wave: Caddy edge, readiness scoring, real ML stack, infra audit+tuning
- WS-A (explore): audit 11 infra components + ML/GNN state + per-module business-rule completeness → feeds scoring doc.
- WS-B (coder): Caddy edge layer — deploy/caddy/ (Caddyfile template, on-demand TLS gated by control-plane domain verify, static frontend serving, API passthrough to APISIX). Owns deploy/caddy/ + docs/architecture/edge-caddy.md only.
- WS-C (coder): ml/ training stack — synthetic Nigerian data generators, PyTorch models (fraud GNN, credit scorer, LUC AVM, crowd density), real training loops, Ray distributed, lakehouse extract pipeline, continuous training. Owns ml/ only. Contract: artifacts → ml/artifacts/*.pt + model_card.json.
- WS-D (coder): services/mod-ml-inference/ — CPU-only FastAPI inference serving loading ml/artifacts, MLflow registry client (fail-closed), drift monitoring, A/B routing. Owns services/mod-ml-inference/ only.
- WS-E (coder): deploy/tuning/ — tuned configs for postgres, mysql (mojaloop), tigerbeetle, redis, kafka, fluvio, apisix, openappsec, keycloak, permify, opensearch, temporal, dapr + docs/operations/performance-tuning.md. Owns deploy/tuning/ + that doc only.
- Integration (orchestrator): compose/helm wiring for caddy + ml-inference + mlflow server, scoring doc docs/delivery/production-readiness-review.md, docs updates, gates, push.

## Stage 13 — Land Management best-of-both (munisp/landmanagement gap closure)
- Analyze munisp/landmanagement (IDLR-PTS TS monorepo: 105 features — parcels, titling, docs/OCR, blockchain anchoring, mortgages, escrow, disputes, GeoAI, drone ODM, gov integrations).
- Platform current state: mod-gis-lands (cadastre + e-C-of-O titling workflow + Ed25519 signing + SLA), mod-gis-luc (LUC calc), mod-geospatial (PostGIS/Sedona/GeoLibre/H3), luc_avm ML.
- Gaps chosen for implementation (best-of-both, platform patterns: FastAPI, fail-closed adapters, X-State-Tenant, kobo integers, hash-chained audit, AsyncAPI events):
  1. services/mod-land-docs: document mgmt + OCR seam (PaddleOCR/Docling fail-closed), classification, verification, versioning.
  2. services/mod-mortgage: lien/mortgage lifecycle, credit_mlp seam, disbursement via ledger/fundsflow, repayment, discharge, transfer-block hook.
  3. mod-gis-lands extension: subdivision/merger, ownership history, disputes, title-risk (fraud_gnn seam), hash anchoring seam.
- Orchestrator wiring: contracts (OpenAPI/AsyncAPI regen), validate_packs, docker-compose, helm values, prometheus, docs/scorecard; commit; push with fresh PAT.
