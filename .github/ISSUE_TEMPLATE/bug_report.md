---
name: Bug Report
about: Report a defect in an SOS module, service, or deployment
title: "[BUG] <module/area>: <short summary>"
labels: [bug]
assignees: []
---

## Affected area

- Module / service: <!-- e.g. services/mod-rev-core, ledger/splits, infra/helm -->
- State tenant (if state-specific): <!-- lagos | ogun | osun | benue | nasarawa | taraba -->
- Environment: <!-- staging | FAT env | SAT env | production -->

## Severity (per docs/procurement/sla-slo.md)

- [ ] Sev-1 — payment switch / ledger / gateway outage (MTTA < 15 min, MTTR < 2 h)
- [ ] Sev-2 — major module failure, no workaround (MTTR < 4 h)
- [ ] Sev-3 — degraded performance / partial failure (MTTR < 12 h)
- [ ] Sev-4 — cosmetic / minor (next release cycle)

## What happened

<!-- Observed behavior, with timestamps and request/transaction IDs. -->

## Expected behavior

<!-- Reference the contract (contracts/openapi, contracts/asyncapi) or policy pack
     (config/states/<state>/policy-pack.json) that defines correct behavior. -->

## Reproduction

1. …
2. …

## Evidence

<!-- Logs, screenshots, TigerBeetle transfer IDs, k6/Locust output. Redact all
     citizen PII — NDPA 2023 applies to issue content too. -->

## Provenance

<!-- Tag material figures [LIVE] / [DERIVED] / [GAP] per CONTRIBUTING.md. -->
