# control-plane — Global Tenant Provisioning Engine

**WP-01 / EPIC-01 · Lot 1 · RT-01**

Central orchestration engine for zero-downtime onboarding of state tenants and MDA sub-tenants: automated K8s namespaces, Postgres schemas (RLS), Keycloak realms, S3 buckets, and KMS keyrings.

- **CLI:** `sosctl tenant create --state=nasarawa --tier=shared` (see [`tools/`](../../tools/README.md))
- **API:** `POST /control/v1/tenants`, `POST /control/v1/tenants/{id}/policy-packs` — contract: [`contracts/openapi/control-plane.yaml`](../../contracts/openapi/control-plane.yaml)
- **Events:** `ng.sos.tenant.provisioned` — [`contracts/asyncapi/platform-events.yaml`](../../contracts/asyncapi/platform-events.yaml)
- **Acceptance:** tenant isolation in < 90 s; zero cross-tenant access under default Cilium policies
- **Stack:** Go · Dapr · Kubernetes CRDs · ArgoCD · Unleash
- **Data boundary:** contains ZERO citizen PII or financial balances — metadata and tenant configs only

## Reference implementation (Python/FastAPI)

`app/` implements `contracts/openapi/control-plane.yaml`: tenant lifecycle
(`POST /control/v1/tenants` → provisioning → active/suspended workflow states),
policy-pack registry (`POST /control/v1/tenants/{id}/policy-packs`, schema +
guardrail validated, 422 on failure), an append-only audit-event feed
(`GET /control/v1/audit-events`), and a PII guard middleware that rejects
request bodies with PII-looking fields (HTTP 400) to enforce the data boundary.

```bash
pip install -e services/control-plane[dev]
uvicorn app.main:app --app-dir services/control-plane --port 8000
cd services/control-plane && python3 -m pytest
```

The store is in-memory; production mapping: Postgres metadata schema +
OpenSearch immutable audit archive (7-year retention). Bundle naming mirrors
`tools/sosctl` (implementations are intentionally independent).
