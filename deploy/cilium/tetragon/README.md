# Tetragon policies — NDPA / security-baseline mapping
#
# These TracingPolicies are the runtime-enforcement half of the platform's
# security baseline. The static half lives in
# tests/security/test_security_baseline.py (F-053 / Stage 3 pre-pen-test
# gates). Mapping:
#
# | Tetragon policy                    | Baseline control                              | NDPA relevance |
# |------------------------------------|-----------------------------------------------|----------------|
# | exec-protection.yaml               | No shell/interpreter in payment & ledger pods; complements "no inline secrets" by making live tampering fatal | Integrity of personal-data processing systems (NDPA s.39 security safeguards) |
# | file-integrity.yaml (/etc, /data, postgres) | PII-egress gate protects data at the API layer; this protects it at rest on disk — writes to ledger & DB files by foreign processes are killed/audited | Confidentiality of NIN/BVN-linked records; tamper evidence |
# | privilege-escalation-audit.yaml    | Fail-closed posture mirrors the tenant-isolation negative tests — escalation attempts are recorded with full process ancestry (enableProcessAncestors) | Accountability & auditability of access to personal data |
#
# Event flow: Tetragon exports JSON to stdout (install/values.yaml
# tetragon.export.mode) -> node log pipeline -> OpenSearch, alongside the
# application audit events. Hubble flow logs (dns/http/drop metrics) and
# Tetragon process events together give the "who called what, from which
# process lineage, resolving which name" chain required for NDPA incident
# reporting (72-hour breach-notification window).
#
# Rollout: apply in audit-first order on a shared-tier staging cluster —
# replace `Sigkill` with `Post`, soak for one week, compare against
# expected process inventories, then re-apply enforcement. The security
# baseline suite (pytest tests/security/test_security_baseline.py) must
# stay green before and after each step.
