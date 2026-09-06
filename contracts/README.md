# API & Event Contracts

Contracts-first development: every SOS microservice exposes documented, versioned contracts — satisfying **Clause 19.4** (anti-lock-in, OpenAPI 3.1 obligation) and enabling independent lot delivery.

| Directory | Standard | Contents |
|---|---|---|
| [`openapi/`](openapi/) | OpenAPI 3.1 | REST contracts: revenue assessments, cadastre parcels, control-plane tenant provisioning, plus generated+curated contracts for every implemented module (mod-gis-luc, mod-mining, mod-forestry, mod-market, mod-agri-waybill, mod-transport-wim, mod-health, mod-education, mod-police-cad, mod-mobility-switch — regenerate with [`openapi/generate_from_apps.py`](openapi/generate_from_apps.py)) |
| [`asyncapi/`](asyncapi/) | AsyncAPI 2.x/3.x | Event contracts on `ng.sos.*` topics: mining consignments, tenant lifecycle, payment settlement, forestry provenance & untagged-timber alerts |
| [`policy-packs/`](policy-packs/) | JSON Schema 2020-12 | Dynamic State Policy Pack schemas (statutory revenue splits, fee schedules, OPA Rego hooks) |

## Rules

1. No API change merges without its contract update in the same PR.
2. All monetary amounts are integer **kobo** (`*_kobo`), never float.
3. Every request is tenant-scoped: `state_id` path parameter + JWT tenant claim, enforced by Postgres RLS.
4. All contracts validated in CI (`.github/workflows/ci.yml`) against Spectral/AsyncAPI linters.
