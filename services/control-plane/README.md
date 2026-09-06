# control-plane — Global Tenant Provisioning Engine

**WP-01 / EPIC-01 · Lot 1 · RT-01**

Central orchestration engine for zero-downtime onboarding of state tenants and MDA sub-tenants: automated K8s namespaces, Postgres schemas (RLS), Keycloak realms, S3 buckets, and KMS keyrings.

- **CLI:** `sosctl tenant create --state=nasarawa --tier=shared` (see [`tools/`](../../tools/README.md))
- **API:** `POST /control/v1/tenants`, `POST /control/v1/tenants/{id}/policy-packs` — contract: [`contracts/openapi/control-plane.yaml`](../../contracts/openapi/control-plane.yaml)
- **Events:** `ng.sos.tenant.provisioned` — [`contracts/asyncapi/platform-events.yaml`](../../contracts/asyncapi/platform-events.yaml)
- **Acceptance:** tenant isolation in < 90 s; zero cross-tenant access under default Cilium policies
- **Stack:** Go · Dapr · Kubernetes CRDs · ArgoCD · Unleash
- **Data boundary:** contains ZERO citizen PII or financial balances — metadata and tenant configs only
