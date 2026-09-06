# Secrets management pattern (Stage 7.D)

No plaintext secret values are committed to this repository — ever. The
inline-secret scanner (`tests/security/test_security_baseline.py`) sweeps
`deploy/**` and `infra/**` (including this directory) and fails the build
on any secret-looking literal.

Two interchangeable delivery patterns are supported; both keep ciphertext
or references in git and plaintext only in the secret backend:

1. **ExternalSecrets (preferred)** — `external/*.yaml` declares
   `ExternalSecret` objects that materialise Kubernetes Secrets from the
   cluster secret store (`cluster-secret-store.yaml`; Vault in the
   reference deployment, pluggable to AWS Secrets Manager / GCP SM).
2. **Sealed Secrets** — `sealed/` carries Bitnami SealedSecret examples
   whose `encryptedData` blobs are ciphertext sealed by the target
   cluster's controller key (safe to commit; can only be decrypted by
   that cluster).

See `docs/operations/secrets-management.md` for the operator runbook
(rotation, bootstrap, per-state rollout).

## Canonical live-mode secret sets

| Kubernetes Secret | Consumed by | Keys |
|---|---|---|
| `sos-secrets-kyc` | mod-kyc-kyb (`KYC_REGISTRY_MODE=live`) | `NIMC_BASE_URL`, `NIMC_CLIENT_ID`, `NIMC_CLIENT_SECRET`, `NIMC_MTLS_CERT`, `NIMC_MTLS_KEY`, `CAC_BASE_URL`, `CAC_CLIENT_ID`, `CAC_CLIENT_SECRET`, `CAC_MTLS_CERT`, `CAC_MTLS_KEY` |
| `sos-secrets-ledger` | mod-rev-core, control-plane | `TB_ADDRESSES`, `TB_CLUSTER_ID` |
| `sos-secrets-keycloak` | control-plane, realm-import job | `KEYCLOAK_ADMIN_URL`, `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD` |
| `sos-secrets-storage` | control-plane, lakehouse, backup jobs | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_KMS_KEY_ID`, `SOS_BACKUP_S3_BUCKET` |
| `sos-secrets-kms` | control-plane (envelope encryption) | `KMS_BACKEND`, `KMS_VAULT_ADDR`, `KMS_VAULT_TOKEN` |
| `sos-secrets-opensearch` | control-plane audit archive, snapshot jobs | `OPENSEARCH_URL`, `OPENSEARCH_USER`, `OPENSEARCH_PASSWORD` |
| `sos-secrets-telco` | mod-citizen-portal (USSD/IVR webhooks) | `CITIZEN_PORTAL_TELCO_SECRET`, `TELCO_SECRET_HEADER` |
| `sos-secrets-payments` | mod-mobility-switch (NIBSS e-Bills, FSPIOP/Mojaloop) | `NIBSS_CLIENT_ID`, `NIBSS_CLIENT_SECRET`, `FSPIOP_CALLBACK_SECRET`, `FSPIOP_JWS_SIGNING_KEY` |

Every adapter consuming these fails closed at boot when a required key is
absent (see e.g. `mod-kyc-kyb build_registry_adapters`, control-plane
`archive_from_env`). Wiring into workloads is via `envFrom: secretRef`
on the Helm module template (`.Values.modules.<key>.secretEnvFrom`).
