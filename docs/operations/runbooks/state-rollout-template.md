# Runbook Template: Per-State Rollout

Reference states: `config/states/` — lagos (dedicated-tier), ogun
(hybrid-tier), osun / benue / nasarawa / taraba (shared-tier). Copy this
template into the state's rollout ticket and check items off; attach
evidence links inline.

State: `<name>` | Tier: `<dedicated|hybrid|shared>` | Target date: `<date>`
State platform owner: `<name>` | SOS lead: `<name>`

## Phase 0 — Prerequisites

- [ ] State MOU / gazette reference recorded (policy pack
      `gazette_reference` must cite a real instrument).
- [ ] `config/states/<state>/policy-pack.json` authored and
      `python3 config/states/validate_packs.py` passes.
- [ ] `config/states/<state>/modules.yaml` enables only the procured
      modules.
- [ ] Secret sets for the state provisioned in Vault
      (`docs/operations/secrets-management.md` §Bootstrap).

## Phase 1 — Platform provisioning (see runbooks/tenant-provisioning-failure.md)

- [ ] Postgres `tenant_<state>` schema migrated, RLS verified
      (cross-tenant read attempt must return 0 rows).
- [ ] Keycloak realm `sos-<state>` rendered + imported;
      `infra/tests/validate_infra.py` realm-drift check passes.
- [ ] Overlay `infra/k8s/overlays/<tier>/states/<state>.yaml` carries
      Namespace (tenant label), NetworkPolicy (egress), ResourceQuota,
      and the `sos-<state>-ledger` ConfigMap.
- [ ] ArgoCD Application `sos-<state>` synced & healthy.
- [ ] Backups: tenant appears in `postgres_backup.py` output manifest;
      first restore-verify drill passed.

## Phase 2 — Acceptance

- [ ] FAT evidence attached (`make gates GATE=stage1`).
- [ ] SAT passed for the state (`tests/sat/run_sat.py`).
- [ ] DR gate passed with the state's manifest included
      (`make gates GATE=dr`).
- [ ] Go-live checklist signed (`tests/gates/go_live_checklist.py`).

## Phase 3 — Handover

- [ ] State ops team trained on: this runbook set, the DR runbook, and
      the secrets-rotation procedure.
- [ ] On-call rota + escalation contacts registered.
- [ ] First weekly restore-verify drill scheduled.

## Notes / deviations

`<record every deviation from the template here — an empty section means
a textbook rollout>`
