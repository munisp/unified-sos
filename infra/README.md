# SOS Infrastructure — `infra/`

Infrastructure-as-code for the State Operating System (SOS), organized by the
three tenancy tiers defined in
[`docs/architecture/06-tenancy-security.md`](../docs/architecture/06-tenancy-security.md):

| Tier | States | Model |
|---|---|---|
| Tier 1 — Dedicated Sovereign | Lagos | Dedicated K8s cluster, dedicated Postgres/Patroni, standalone TigerBeetle |
| Tier 2 — Hybrid Industrial | Ogun | Dedicated DB / node pool + shared lakehouse analytics |
| Tier 3 — Shared Multi-Tenant | Osun, Benue, Nasarawa, Taraba | Shared cluster, namespace + RLS isolation, Kubecost pro-rata allocation |

## Layout

```
infra/
├── k8s/                    # Kustomize base + per-tier overlays (namespace, quota, NetworkPolicy, RLS notes)
├── helm/sos-platform/      # Umbrella Helm chart (APISIX, Keycloak realm import, mod-rev-core, Kubecost, KEDA)
├── terraform/              # Modules: sovereign-cluster, object-storage, kms-keyring + per-tier envs
├── gitops/                 # ArgoCD AppProject + per-state Application declarations
└── tests/validate_infra.py # Static validator — yaml-parses every manifest and asserts tenancy invariants
```

## Validate

```bash
pip install pyyaml
python3 infra/tests/validate_infra.py   # exits 0 on success
```

## Rules

- No credentials or secrets in this tree. Secrets are injected at runtime via
  OpenBao/Vault per `CONTRIBUTING.md`.
- Tenant isolation is non-negotiable: every state namespace carries a
  `ResourceQuota` and a `NetworkPolicy` blocking cross-state egress.
- The dedicated tier (Lagos) must never reference shared data-plane resources.
