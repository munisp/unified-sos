# SOS CI/CD Pipelines — `.github/workflows/`

## Pipelines

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push/PR to `main` | **go** (vet/test/build every Go module under `ledger/`, `services/`), **python** (pytest across `services/`, `edge/`, `geospatial/` + `config/states/validate_packs.py` + `infra/tests/validate_infra.py`), **contract-lint** (OpenAPI 3.1 spec validation, AsyncAPI envelope check), **yaml-lint** |
| `security-scan.yml` | push/PR to `main` + nightly | **gitleaks** (secret scanning), **trivy-fs** (CRITICAL/HIGH filesystem scan, SARIF artifact), **license-check** (MIT/Apache-2.0/BSD/ISC/PostgreSQL whitelist stub) |
| `sbom.yml` | release published / manual | **cyclonedx** (anchore/sbom-action → `sbom.cyclonedx.json` attached to the release) |

All third-party actions are pinned to full-length commit SHAs. Caching:
`actions/setup-go` module cache, `actions/cache` for pip.

## Mapping pipeline gates to procurement acceptance stages

The five-stage acceptance lifecycle
([`docs/procurement/acceptance-framework.md`](../../docs/procurement/acceptance-framework.md))
is enforced partially in CI and partially in environment-specific test suites
under `tests/`. This is the mapping:

| Acceptance stage | Procurement gate | CI gate in this directory |
|---|---|---|
| **Stage 1 — FAT** (factory acceptance) | Microservice unit coverage >85%; OpenAPI schema validation; container vulnerability scanning | `ci.yml` **go**/**python** test jobs; `ci.yml` **contract-lint**; `security-scan.yml` **trivy-fs** |
| **Stage 2 — Performance & stress** | 500k TPS TigerBeetle; 50k req/s APISIX; 10k concurrent Sedona joins | Not in CI — k6/Locust specs under `tests/` run in the staging environment |
| **Stage 3 — Security & pen-test** | Zero critical/high vulnerabilities; OWASP Top 10 | `security-scan.yml` **trivy-fs** (blocks CRITICAL/HIGH) + **gitleaks**; CREST pen-test is external |
| **Stage 4 — SAT** (site acceptance) | Hardware integration; live bank settlement | Environment-specific; wave gates per `docs/delivery/rollout-matrix.md` |
| **Stage 5 — UAT & go-live** | Steering-committee sign-off | Manual; requires green CI + SBOM artifact attached to the release |
| **SBOM & open-source governance** | Signed CycloneDX v1.5 / SPDX v2.3 SBOM per deliverable; license whitelist | `sbom.yml` (SBOM artifact) + `security-scan.yml` **license-check** |

## Invariants enforced in every PR

- `config/states/validate_packs.py` — every state policy pack validates against
  `contracts/policy-packs/revenue-split.schema.json` and the concession
  guardrails (8% Lagos/Ogun, 15% agrarian/extractive; INSTANT legs ≤ 100%).
- `infra/tests/validate_infra.py` — every state overlay has a NetworkPolicy +
  ResourceQuota; the dedicated tier (Lagos) never references shared data-plane
  resources; GitOps Applications point at the right overlays/namespaces.
- No secrets (gitleaks), no non-whitelisted licenses (license-check),
  contracts parse and validate (contract-lint).
