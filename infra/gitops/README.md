# GitOps — `infra/gitops/`

ArgoCD AppProject (`projects/`) and per-state Applications (`applications/sos-<state>.yaml`)
for the six SOS state tenants. Each Application pairs the state's kustomize
overlay with the `sos-platform` Helm release pinned to its `tenantStateId`.

## Keycloak realm drift alerting

Per-state Keycloak realms are generated, not hand-written: the canonical
template is `deploy/keycloak/templates/realm-sos-state.json.tmpl` and
`deploy/keycloak/render_realms.py` renders the six realm JSONs into
`deploy/keycloak/realms/` (bundled copies live under
`infra/helm/sos-platform/realms/` for the chart's `.Files.Get`, consumed by
the `sos-keycloak-realms` ConfigMap and the realm-import Job hook).

Drift wiring:

- `python3 deploy/keycloak/render_realms.py --check` exits non-zero when any
  rendered realm differs from the template output. It is invoked from
  `infra/tests/validate_infra.py` (`check_realm_drift`), which runs in CI on
  every PR touching `deploy/keycloak/`, `config/states/`, or `infra/`.
- Never edit `deploy/keycloak/realms/*.json` or the chart-bundled copies
  directly — change the template or the state's `policy-pack.json` /
  `modules.yaml` and re-run the renderer, then re-copy into the chart.
- Client secrets are intentionally absent from the realm JSONs: the
  `sos-services` secret is injected per state from the external secret
  reference `sos-<state>-sos-services`; rotation therefore never requires a
  realm re-render or an ArgoCD sync of the ConfigMap.
