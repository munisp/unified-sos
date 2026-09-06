# Security Policy — State Operating System (SOS)

SOS processes sovereign citizen identities, land registries, and state fiscal transactions. Security is a contractual, statutory obligation — not a feature.

## Reporting a Vulnerability

- **Do NOT open public issues for security vulnerabilities.**
- Report privately via GitHub Security Advisories on this repository, or to the SOS Security Operations Center (SOC) contact published in the engagement channel for your state deployment.
- Include: affected component/module, reproduction path, impact assessment, and any suggested remediation.

## Response Commitments (mapped to contractual CVSS SLAs)

| Severity (CVSS v3.1) | Acknowledge | Patch Release |
|---|---|---|
| Critical (9.0–10.0) | < 4 hours | 24 hours |
| High (7.0–8.9) | < 24 hours | 72 hours |
| Medium (4.0–6.9) | < 3 business days | 14 business days |
| Low (0.1–3.9) | Next triage | Next sprint release |

Sev-1 operational incidents (payment switch, ledger kernel, or API gateway outage halting revenue collection) carry **<15 min MTTA** per the platform SLA.

## Security Architecture Baseline

- **Zero-trust perimeter:** Apache APISIX + OpenAppSec ML-WAF (OWASP Top 10, zero-day ML inspection) — 100% block rate required at acceptance.
- **Sovereign IAM:** Keycloak multi-realm (one realm per state, e.g. `sos-lagos`, `sos-nasarawa`); OIDC/OAuth2 PKCE; mandatory MFA for all MDA administrative roles; FIDO2 hardware tokens for revenue approval officers.
- **Data isolation:** schema-per-tenant or database-per-tenant with PostgreSQL Row-Level Security; Cilium network policies block cross-tenant egress by default.
- **SOC stack:** Wazuh XDR agent fleet, OpenCTI threat intelligence, OpenSearch immutable audit archive; 7-year tamper-evident fiscal audit trail retention.
- **Secrets:** OpenBao / HashiCorp Vault with per-state KMS keyrings; no secrets in source control.
- **Supply chain:** Harbor registry with Trivy scanning and Cosign image signing; signed SBOM (CycloneDX v1.5 / SPDX v2.3) on every release.
- **Data protection:** Nigeria Data Protection Act (NDPA) 2023 — strict in-country data residency; no extraterritorial transfer of citizen PII without written authorization from the State Attorney General and the NDPC.

## Supported Versions

| Stream | Status |
|---|---|
| `main` | Actively supported |
| Release branches `release/wave-*` | Supported per state concession SLA |

## Scope Notes

State-specific deployment vulnerabilities (realm misconfiguration, tenant policy-pack errors) should be reported with the affected state tenant ID but **never** with real citizen PII, parcel owner records, or ledger account data.
