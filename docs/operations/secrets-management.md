# Secrets Management (Stage 7.D)

Pattern reference: `infra/secrets/README.md`. Manifests:
`infra/secrets/cluster-secret-store.yaml`, `infra/secrets/external/*.yaml`
(preferred) and `infra/secrets/sealed/` (alternative). **No plaintext
secret value may ever be committed** — the inline-secret scan in
`tests/security/test_security_baseline.py` covers `infra/**` and fails CI
on a hit.

## Bootstrap (per cluster)

1. Install the external-secrets operator (namespace `external-secrets`,
   service account `external-secrets-operator`).
2. Create the Vault kubernetes-auth role `external-secrets-operator`
   bound to that service account; load the KV tree `sos/<area>/...` with
   the real values (via `vault kv put` from an operator workstation —
   never via git).
3. Apply `infra/secrets/cluster-secret-store.yaml`, then
   `infra/secrets/external/*.yaml`. Verify each target Secret
   materialises: `kubectl -n sos-platform get secret sos-secrets-kyc -o name`.
4. Alternative (no external-secrets): seal values with `kubeseal`
   against the cluster controller and commit the resulting SealedSecrets
   (see `sealed/sealed-secret-example.yaml`).

## Workload wiring

Module deployments mount whole secrets via `envFrom.secretRef` driven by
`.Values.modules.<module>.secretEnvFrom` (see
`infra/helm/sos-platform/templates/_module-deployment.tpl` and the
`secretEnvFrom` entries in `values.yaml`). Non-secret config stays in the
per-state overlay ConfigMaps (`infra/k8s/overlays/*/states/<state>.yaml`).

Every consumer fails closed at boot when a required key is missing
(`mod-kyc-kyb` registry adapters, control-plane audit archive, mobility
switch scheme adapters, telco webhook auth) — a secret that failed to
materialise is a boot failure and an alert, never a silent degraded mode.

## Rotation

| Secret set | Cadence | Procedure |
|---|---|---|
| `sos-secrets-kyc` (NIMC/CAC) | 90 d or on vendor notice | Update KV; ExternalSecret refresh (1 h) rolls pods via checksum annotation; verify `/healthz` per pod |
| `sos-secrets-keycloak` | 90 d | Rotate admin password in Keycloak, update KV, restart control-plane |
| `sos-secrets-storage` (S3 + KMS) | 90 d; KMS key annually | Dual-credential overlap window; update KV; verify a `postgres_backup.py --execute` upload succeeds |
| `sos-secrets-telco` | 180 d | Coordinate with telco aggregator; overlap window where both secrets accepted is NOT supported — schedule downtime |
| `sos-secrets-payments` (NIBSS/FSPIOP) | Per scheme rules | Re-key JWS signing key with scheme coordinator; update KV; run scheme smoke tests |
| SealedSecrets controller key | Backed up per cluster build | Losing the controller key requires re-sealing every SealedSecret — prefer ExternalSecrets for new sets |

## Incident response (suspected leak)

1. Rotate the affected set immediately (procedure above).
2. Audit access: Vault audit log for the KV path; Keycloak/admin events;
   the OpenSearch audit chain (`sosctl audit verify-chain`) must verify —
   treat a chain break as a separate tamper incident
   (`runbooks/audit-chain-tamper-alert.md`).
3. File a security incident per `SECURITY.md`; secrets committed to git
   require history scrub + immediate rotation (assume compromised).
