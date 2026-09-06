# API & Event Contracts

Contracts-first development: every SOS microservice exposes documented, versioned contracts — satisfying **Clause 19.4** (anti-lock-in, OpenAPI 3.1 obligation) and enabling independent lot delivery.

| Directory | Standard | Contents |
|---|---|---|
| [`openapi/`](openapi/) | OpenAPI 3.1 | REST contracts: revenue assessments, cadastre parcels, control-plane tenant provisioning |
| [`asyncapi/`](asyncapi/) | AsyncAPI 2.x/3.x | Event contracts on `ng.sos.*` topics: mining consignments, tenant lifecycle, payment settlement |
| [`policy-packs/`](policy-packs/) | JSON Schema 2020-12 | Dynamic State Policy Pack schemas (statutory revenue splits, fee schedules, OPA Rego hooks) |

## Rules

1. No API change merges without its contract update in the same PR.
2. All monetary amounts are integer **kobo** (`*_kobo`), never float.
3. Every request is tenant-scoped: `state_id` path parameter + JWT tenant claim, enforced by Postgres RLS.
4. All contracts validated in CI (`.github/workflows/ci.yml`) against Spectral/AsyncAPI linters.
