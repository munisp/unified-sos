# ADR-003: Interoperable Payment Switch — Mojaloop

**Status:** Accepted · **Domain:** Payments & Clearing

## Decision

**Mojaloop v16+** (FSPIOP / ISO 20022) as the open-source subnational payment hub.

## Rationale

Executes atomic multi-leg statutory splits across State Consolidated Revenue Fund, MDA retention, and PPP concessionaire escrow accounts; interoperable with NIBSS, Remita, Interswitch, and commercial banks out of the box.

## Tradeoff

Operational complexity — mitigated via turnkey Kubernetes Helm deployment charts (`infra/helm/`).

## Consequences

- Payment clearing workflow: quote → payer commit → TigerBeetle split signal → MDA webhook.
- Acceptance: end-to-end settlement < 50 ms (p99); NIBSS settlement-sheet reconciliation automated.
