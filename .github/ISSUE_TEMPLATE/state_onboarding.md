---
name: State Onboarding
about: Track onboarding of a state tenant (new or existing pilot state)
title: "[ONBOARD] <state>: <milestone>"
labels: [state-onboarding]
assignees: []
---

## State & tenancy tier

- State: <!-- lagos | ogun | osun | benue | nasarawa | taraba | new -->
- Tenancy tier: <!-- Tier 1 dedicated | Tier 2 hybrid | Tier 3 shared
       (docs/architecture/06-tenancy-security.md) -->
- Target rollout wave: <!-- per docs/delivery/rollout-matrix.md -->

## Zero-Capex Concession Structuring Gate

No state is provisioned without all three:

- [ ] Executed PPP Concession Agreement
- [ ] Gazetted statutory revenue-split order (escrows concessionaire fees via
      TigerBeetle) — reference in `config/states/<state>/policy-pack.json`
      `gazette_reference`
- [ ] Approved NDPA Data Protection Compliance Statement

## Provisioning checklist

- [ ] `config/states/<state>/` policy pack + modules.yaml + README created and
      `python3 config/states/validate_packs.py` passes
- [ ] Tenancy overlay present under `infra/k8s/overlays/<tier>-tier/` with
      NetworkPolicy + ResourceQuota; `python3 infra/tests/validate_infra.py` passes
- [ ] ArgoCD Application `sos-<state>` declared under `infra/gitops/applications/`
- [ ] Keycloak realm `sos-<state>` import job configured (infra/helm/sos-platform)
- [ ] TigerBeetle chart-of-accounts initialized per gazetted split order
      (ledger/chart-of-accounts.md)
- [ ] Kubecost allocation labels applied for cost reporting

## Wave-gate evidence

<!-- Which gate criteria must be met before activation? Link load-test and
     FAT/SAT results in tests/. -->
