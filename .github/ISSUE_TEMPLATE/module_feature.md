---
name: Module Feature
about: Propose or scope a feature for a mod-* service module
title: "[FEAT] <module>: <short summary>"
labels: [enhancement, module]
assignees: []
---

## Module & work package

- Module: <!-- e.g. mod-gis-lands -->
- Work package reference: <!-- WP-xx from docs/delivery/work-packages.md -->

## Problem statement

<!-- Which revenue/efficiency leakage or citizen-service gap does this close?
     Link the opportunity ID (e.g. T1, N3, L4) from docs/ppp-pipeline/ if any. -->

## Proposed change

<!-- Behavior change, API surface, events. Remember: all API changes require
     contract updates in contracts/ in the same PR (OpenAPI 3.1 / AsyncAPI). -->

## 80/20 rule check

- [ ] This is shared-core configuration usable by all states (≥80% shared)
- [ ] State variation is expressed via config/states/ policy packs, not code
- [ ] If beyond 20% per-state customization: product-management escalation linked

## Tenancy & data isolation impact

- [ ] New tables include tenant_state_id + RLS policies (db/migrations/)
- [ ] No cross-state data flows introduced
- [ ] NDPA-sensitive fields identified, with compliance note

## Acceptance mapping

<!-- Which FAT/SAT/stress acceptance criteria does this feed?
     (docs/procurement/acceptance-framework.md, tests/) -->

## Provenance

<!-- Tag material figures [LIVE] / [DERIVED] / [GAP] per CONTRIBUTING.md. -->
