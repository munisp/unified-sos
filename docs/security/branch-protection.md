# Required branch-protection settings (security gates)

The blocking security gates introduced in P1 Workstream 5 only block merges
if branch protection on `main` requires them. Configure:

- **Require a pull request before merging** (no direct pushes).
- **Require status checks to pass**, with these checks marked *required*:
  - `Secret scanning (gitleaks)`
  - `Filesystem vulnerability scan (trivy)` — blocking exit-code 1 on
    CRITICAL/HIGH; SARIF upload is `if: always()` so findings stay visible.
  - `Dependency review (blocking)` — fails on High-severity dependency
    changes and copyleft licenses.
  - `SAST — Semgrep OWASP ruleset (blocking)` — OWASP Top 10 + security-audit.
  - `OWASP ZAP baseline scan (blocking on High)` — runs against the ephemeral
    compose stack; fails on High alerts (ZAP exit-code 1).
  - `Vulnerability scan of SBOM (grype, blocking on High)` — grype scan of the
    CycloneDX SBOM, severity cutoff High.
- **Require branches to be up to date before merging** (prevents merging
  against stale, unscanned heads).

No security workflow may use `continue-on-error: true`; this is enforced by
`tests/ci/test_workflows_blocking.py`.
