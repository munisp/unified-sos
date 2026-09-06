# ADR-007: Ingress & API Security — APISIX + OpenAppSec WAF

**Status:** Accepted · **Domain:** Edge Security

## Decision

**Apache APISIX** dynamic Lua/Wasm plugin-based API gateway integrated with **OpenAppSec** machine-learning WAF.

## Rationale

Inspects HTTP/gRPC payloads for SQLi, parameter tampering, and credential stuffing; dynamic tenant routing, TLS termination, DDoS defense. Acceptance requires 100% block rate on OWASP Top 10 automated attack injection with <2 ms gateway latency overhead.

## Tradeoff

Ingress proxy introduces ~1.2 ms latency overhead per request — budgeted within the <50 ms end-to-end payment latency KPI.
