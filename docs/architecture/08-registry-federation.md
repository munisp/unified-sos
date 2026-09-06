# Registry Federation (NIMC / CAC / Sanctions)

KYC/KYB verification depends on national registries. The integration is
**fail-closed at boot and at call time**; the deterministic local default is
fixtures so development and CI never touch live registries.

## Modes

- `KYC_REGISTRY_MODE=fixture` (default) — `FixtureRegistryAdapter`; no
  network, deterministic statuses.
- `KYC_REGISTRY_MODE=live` — mod-kyc-kyb constructs `NimcClient` /
  `CacClient` from `NIMC_BASE_URL`/`NIMC_CLIENT_ID`/`NIMC_CLIENT_SECRET` and
  `CAC_BASE_URL`/`CAC_CLIENT_ID`/`CAC_CLIENT_SECRET` (plus optional
  `*_MTLS_CERT`/`*_MTLS_KEY`). Boot **fails listing every missing var**.

mod-identity has the analogous `IDENTITY_FEDERATION_MODE=fixture|live` seam
(`KEYCLOAK_BASE_URL`/`KEYCLOAK_CLIENT_ID`/`KEYCLOAK_CLIENT_SECRET`) for
per-state Keycloak client-credentials token exchange and NIN claim
verification behind `IdentityFederationClient`.

## Hashing contract

Registry clients never return raw payloads or PII. Responses are normalized
to `{status, confidence, <identifier>_sha256}` dicts; adapters store only the
SHA-256 response hash in `RegistryVerification.response_hash`. Raw NIN / RC
numbers never appear in logs or API responses.

## Resilience

Each live client implements: per-call timeout, bounded exponential-backoff
retry, and a circuit-breaker counter that fails closed after consecutive
transport/auth failures. Timeouts and 401/403 responses raise
`AdapterUnavailableError` (surfaced as the `REGISTRY_UNAVAILABLE` error
schema in `contracts/openapi/mod-kyc-kyb.yaml`); malformed upstream payloads
normalize to `status=UNAVAILABLE` with zero confidence.

## Metering

mod-identity metered `KYC_ADJUNCT` verifications record
`registry_latency_ms` in the hash-chained audit detail for per-call
observability of federation latency.
