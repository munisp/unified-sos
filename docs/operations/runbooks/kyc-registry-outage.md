# Runbook: KYC Registry Outage (NIMC / CAC)

**Trigger:** mod-kyc-kyb health checks failing in `KYC_REGISTRY_MODE=live`,
or adapter error-rate alert on `NimcClient` / `CacClient`.

1. **Classify.** Is it NIMC, CAC, or both? Is it connectivity, auth
   (401/403 — likely rotated credentials in `sos-secrets-kyc`), or mTLS
   certificate expiry (`NIMC_MTLS_CERT` / `CAC_MTLS_CERT`)?
2. **Confirm adapter behaviour.** Adapters fail closed: verification
   requests that cannot reach a registry are rejected, not auto-approved.
   Expected user impact: new KYC/KYB verifications fail; previously
   verified identities remain valid.
3. **Credentials path.** If auth errors: check ExternalSecret freshness —
   `kubectl -n sos-platform get externalsecret sos-secrets-kyc` (Ready
   column) and the last sync time. Re-sync or re-seal; restart
   mod-kyc-kyb. Never disable fail-closed checks to "restore service".
4. **Vendor outage path.** If NIMC/CAC confirm a national outage: enable
   the documented degraded mode — queue verification requests and notify
   applicants (mod-citizen-portal banner). Degraded mode requires a
   platform-lead approval note in the incident ticket; it never auto-
   approves identities.
5. **Recovery.** When the registry is back, drain the verification queue
   in submission order; watch the adapter error rate for 30 min before
   clearing the incident.
6. **mTLS renewal** (common root cause): obtain the new client
   certificate from the registry operator, update the Vault KV path
   `sos/registries/kyc`, verify the ExternalSecret re-synced, and roll
   mod-kyc-kyb.
