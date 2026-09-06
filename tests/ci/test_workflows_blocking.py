"""CI gate assertions for the security workflows (P1 Workstream 5).

These tests fail if any security workflow regresses to advisory mode
(`continue-on-error: true`), if the OWASP ZAP / Semgrep / dependency-review /
grype blocking jobs are removed, or if the SBOM workflow loses its
pull_request trigger.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SECURITY_WORKFLOWS = ["security-scan.yml", "sbom.yml"]


def load_workflow(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.exists(), f"missing workflow {name}"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{name} did not parse to a mapping"
    return data


@pytest.mark.parametrize("workflow", SECURITY_WORKFLOWS)
def test_workflow_parses(workflow: str) -> None:
    load_workflow(workflow)


@pytest.mark.parametrize("workflow", SECURITY_WORKFLOWS)
def test_no_continue_on_error(workflow: str) -> None:
    text = (WORKFLOWS / workflow).read_text().lower()
    assert "continue-on-error: true" not in text, (
        f"{workflow} must be blocking; continue-on-error: true is forbidden"
    )


def test_security_scan_blocking_jobs() -> None:
    wf = load_workflow("security-scan.yml")
    jobs = wf.get("jobs", {})

    for job_id in ("zap-baseline", "semgrep", "dependency-review", "trivy-fs"):
        assert job_id in jobs, f"security-scan.yml missing blocking job {job_id!r}"
        job = jobs[job_id]
        # A job that cannot fail is advisory only.
        assert job.get("continue-on-error") is not True
        for step in job.get("steps", []):
            assert step.get("continue-on-error") is not True, (
                f"{job_id} step {step.get('name')!r} must not swallow failures"
            )

    # ZAP baseline must fail the action on High findings.
    zap = jobs["zap-baseline"]
    zap_steps = [s for s in zap["steps"] if "zaproxy/action-baseline" in str(s.get("uses", ""))]
    assert zap_steps, "zap-baseline job does not run zaproxy/action-baseline"
    assert zap_steps[0].get("with", {}).get("fail_action") == "true", (
        "ZAP baseline must set fail_action: 'true' (exit-code 1 on High)"
    )

    # Semgrep must run the OWASP ruleset.
    semgrep = jobs["semgrep"]
    config = " ".join(
        str(s.get("with", {}).get("config", "")) for s in semgrep["steps"]
    )
    assert "owasp" in config.lower(), "semgrep job must run the OWASP ruleset"

    # Trivy SARIF upload stays if: always(), scan step keeps blocking exit-code.
    trivy = jobs["trivy-fs"]
    steps = trivy["steps"]
    scan = next(s for s in steps if "trivy-action" in str(s.get("uses", "")))
    assert scan.get("with", {}).get("exit-code") == "1"
    upload = next(s for s in steps if "upload" in str(s.get("name", "")).lower())
    assert upload.get("if") == "always()"


def test_sbom_workflow_pr_trigger_and_blocking_grype() -> None:
    wf = load_workflow("sbom.yml")
    on = wf.get("on", {})
    # YAML 1.1 parses the bare key `on` as True.
    if on is None or on == {}:
        on = wf.get(True, {})
    assert "pull_request" in on, "sbom.yml must generate SBOMs on pull_request"

    jobs = wf.get("jobs", {})
    grype = jobs.get("grype-scan")
    assert grype, "sbom.yml missing blocking grype-scan job"
    assert grype.get("continue-on-error") is not True
    scan_steps = [s for s in grype["steps"] if "anchore/scan-action" in str(s.get("uses", ""))]
    assert scan_steps, "grype-scan job does not use anchore/scan-action"
    with_block = scan_steps[0].get("with", {})
    assert with_block.get("fail-build") is True
    assert str(with_block.get("severity-cutoff", "")).lower() == "high"


def test_sbom_cosign_signing_on_release() -> None:
    wf = load_workflow("sbom.yml")
    jobs = wf.get("jobs", {})
    signer = jobs.get("sign-release-sbom")
    assert signer, "sbom.yml missing cosign signing job"
    assert signer.get("if") == "github.event_name == 'release'"
    text = (WORKFLOWS / "sbom.yml").read_text()
    assert "cosign" in text and "sign-blob" in text, "keyless cosign signing missing"
