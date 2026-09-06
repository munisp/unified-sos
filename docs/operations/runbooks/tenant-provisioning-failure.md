# Runbook: Tenant Provisioning Failure

**Trigger:** `sosctl tenant provision <state>` (tools/sosctl) exits
non-zero, or a new state's ArgoCD Application stays
Degraded/Progressing past the provisioning window.

Provisioning order (each step is idempotent; re-run the failed step, not
the whole pipeline):

1. **Config validation.** `python3 config/states/validate_packs.py` must
   pass for the state's policy pack. Fix the pack — never bypass
   guardrails (revenue-share ceiling, IRR cap).
2. **Postgres schema.** Migration runner applies `db/migrations` into
   `tenant_<state>` and enables RLS. Failure here is usually a
   conflicting object from an aborted earlier attempt: drop the
   half-created schema in the staging cluster only and re-run.
3. **Keycloak realm.** Render + import: `deploy/keycloak/render_realms.py`
   then the realm-import Job from the Helm chart. Drift check:
   `infra/tests/validate_infra.py` §keycloak realm drift must pass.
4. **TigerBeetle wiring.** The overlay ConfigMap `sos-<state>-ledger`
   must set `TB_ADDRESSES` and `TB_CLUSTER_ID=1`; mod-rev-core fails
   closed (refuses to boot with the ledger enabled) when these are
   missing — a CrashLoopBackOff on mod-rev-core right after provisioning
   is almost always this.
5. **Secrets.** The `sos-secrets-*` sets must exist in the namespace
   before module rollout (ExternalSecrets Ready, or SealedSecrets
   decrypted). A missing secret = boot failure by design.
6. **ArgoCD sync.** Application `sos-<state>` with automated
   prune+selfHeal. Verify destination namespace `sos-<state>` and that
   the dedicated tier (Lagos) shows no shared data-plane references.
7. **Acceptance.** Run the SAT harness scoped to the state
   (`tests/sat/run_sat.py`) before handing the tenant to the state team;
   attach evidence to the onboarding ticket.

**Rollback:** remove the ArgoCD Application (prune deletes workloads),
drop `tenant_<state>` schema, delete the realm. Ledger accounts are never
deleted — mark them closed via `sosctl ledger` instead.
