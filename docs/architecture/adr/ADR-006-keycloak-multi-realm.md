# ADR-006: Zero-Trust Identity & Access — Keycloak Multi-Realm

**Status:** Accepted · **Domain:** Identity & Security

## Decision

**Keycloak multi-realm OIDC/OAuth2**: each state is provisioned an isolated realm (e.g., `realm-ogun`, `realm-lagos`) with federated login via NIMC National Identification Number (NIN) and CAC APIs.

## Rationale

Realm-per-state delivers sovereignty by construction: zero cross-realm token leakage, per-state MFA/FIDO2 policy, and MDA hierarchical RBAC. JWTs carry cryptographic state-tenant-ID and MDA-boundary claims consumed by Dapr authorization and Postgres RLS.

## Tradeoff

Realm configuration sync automated via the Keycloak Operator under GitOps (`infra/gitops/`).
