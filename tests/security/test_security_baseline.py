"""Security baseline gates (F-053 / Stage 3 pre-pen-test blocking checks).

All gates are deterministic and local; every check is blocking (a failure
fails the suite — nothing is silently skipped):

1. **PII egress** — no FastAPI *response* model exposed by any service may
   carry raw NIN/BVN/MSISDN fields (only masked/hashed derivatives).
2. **Tenant isolation (negative tests)** — cross-state access on
   mod-citizen-portal, mod-ppp-investment and mod-police-cad must fail
   closed and must not leak the other tenant's data.
3. **No inline secrets** — regex scan of deploy/** and infra/** for private
   key material and hardcoded credentials (dev-only sentinel values and
   ``${VAR}`` references are explicitly allow-listed).
4. **Webhook auth** — the USSD/IVR telco callbacks reject traffic without
   the shared secret (403), including fail-closed behaviour when
   CITIZEN_PORTAL_TELCO_SECRET is unset.
"""
import os
import re
from pathlib import Path

import pytest

import importlib.util
import sys

_spec = importlib.util.spec_from_file_location(
    "contract_apputil", Path(__file__).resolve().parents[1] / "contract" / "apputil.py"
)
_apputil = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_apputil)
REPO_ROOT = _apputil.REPO_ROOT
load_service_app = _apputil.load_service_app

from fastapi.testclient import TestClient  # noqa: E402

SERVICES_WITH_APPS = [
    "mod-agri-waybill", "mod-citizen-portal", "mod-education", "mod-environment",
    "mod-erp-bridge", "mod-forestry", "mod-geospatial", "mod-health", "mod-identity", "mod-kyc-kyb",
    "mod-market", "mod-mining", "mod-mobility-switch", "mod-police-cad",
    "mod-ppp-investment", "mod-transparency", "mod-transport-wim",
]

# ---------------------------------------------------------------------------
# 1. PII egress scan
# ---------------------------------------------------------------------------

#: Raw-PII field-name patterns that must never appear in a read/response model.
PII_FIELD = re.compile(r"^(nin|bvn|msisdn|phone_number|phone|account_number)$", re.I)
#: Explicitly safe derivatives (masked/hashed forms).
SAFE_PII_FIELD = re.compile(r"(masked_|_hash$|_hint$|hash_)", re.I)


def _response_models(service: str):
    module = load_service_app(service)
    app = module.create_app()
    for route in getattr(app, "routes", []):
        model = getattr(route, "response_model", None)
        if model is not None:
            yield route.path, model


@pytest.mark.parametrize("service", SERVICES_WITH_APPS)
def test_read_models_exclude_raw_pii(service):
    leaks = []
    for path, model in _response_models(service):
        fields = getattr(model, "model_fields", {}) or {}
        for name in fields:
            if PII_FIELD.match(name) and not SAFE_PII_FIELD.match(name):
                leaks.append(f"{path}: {model.__name__}.{name}")
    assert not leaks, f"{service}: raw PII fields in read models: {leaks}"


# ---------------------------------------------------------------------------
# 2. Tenant-isolation negative tests
# ---------------------------------------------------------------------------

def test_citizen_portal_cross_state_payroll_audit_is_forbidden():
    mod = load_service_app("mod-citizen-portal")
    with TestClient(mod.create_app()) as c:
        r = c.post("/citizen/v1/payroll-audits", json={"state_id": "lagos"})
        assert r.status_code == 201, r.text
        audit_id = r.json()["audit_id"]
        # Same tenant can read it.
        assert c.get(f"/citizen/v1/payroll-audits/{audit_id}",
                     params={"state_id": "lagos"}).status_code == 200
        # Cross-tenant access is blocked (403) and leaks nothing.
        r = c.get(f"/citizen/v1/payroll-audits/{audit_id}", params={"state_id": "ogun"})
        assert r.status_code in (403, 404), r.text
        assert "lagos" not in r.text.lower() or r.status_code == 403


def test_ppp_cross_state_stage_advance_is_forbidden():
    mod = load_service_app("mod-ppp-investment")
    project = {
        "project_id": "PPP-LAG-REDLINE-01",
        "state_id": "lagos",
        "title": "Lagos Red Line O&M Concession",
        "sector": "TRANSPORT",
        "description_public": "Operate-and-maintain concession for the Red Line.",
        "sponsoring_agency": "LAMATA",
    }
    with TestClient(mod.create_app()) as c:
        assert c.post("/projects", json=project).status_code == 201
        r = c.post(f"/projects/{project['project_id']}/stage",
                   json={"state_id": "ogun", "stage": "OBC"})
        assert r.status_code in (403, 404), r.text
        # Internal fields must not leak via the error either.
        assert "internal" not in r.text.lower()


def test_police_cad_cross_state_dispatch_fails_closed():
    """Cross-tenant dispatch is rejected fail-closed with 403 (or 404).

    mod-police-cad raises ``CrossTenantError`` (mapped to HTTP 403, matching
    the mod-ppp-investment TenantIsolationError pattern); unknown
    incident/unit IDs return 404 (no enumeration). The gate also asserts no
    dispatch was logged and no cross-tenant data leaks via the audit feed.
    """
    mod = load_service_app("mod-police-cad")
    with TestClient(mod.create_app()) as c:
        inc = c.post("/cad/v1/incidents", json={
            "tenant_state_id": "lagos", "agency": "LNSC", "category": "ROBBERY",
            "latitude": 6.50, "longitude": 3.40,
        })
        assert inc.status_code == 201, inc.text
        unit = c.post("/cad/v1/units", json={
            "tenant_state_id": "ogun", "agency": "SO-SAFE", "call_sign": "OG-UNIT-7",
            "personnel_count": 4, "biometric_enrolled": True,
        })
        assert unit.status_code == 201, unit.text

        r = c.post("/cad/v1/dispatch", json={
            "incident_id": inc.json()["incident_id"],
            "unit_id": unit.json()["unit_id"],
            "latitude": 6.50, "longitude": 3.40,
        })
        assert r.status_code in (403, 404), (
            f"cross-tenant dispatch must fail closed with 403/404, got {r.status_code}: {r.text}")
        assert c.get("/cad/v1/dispatch-log").json() == []

        # Trust-fund audit feeds are per-tenant: ogun must not see lagos funds.
        c.post("/cad/v1/trust-fund/donations", json={
            "tenant_state_id": "lagos", "donor_ref": "DNR-LAG-001", "amount_kobo": 5_000_000,
        })
        ogun_feed = c.get("/cad/v1/trust-fund/ogun/audit-feed").json()
        assert ogun_feed.get("balance_kobo", 0) in (0, None) or not ogun_feed.get("donations")


# ---------------------------------------------------------------------------
# 3. No inline secrets in deploy/** and infra/**
# ---------------------------------------------------------------------------

SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|token|private[_-]?key)\b"
    r"\s*[:=]\s*['\"]?([^\s'\"#]+)"
)
PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
#: Explicitly allow-listed dev sentinels / placeholders.
ALLOWED = ("dev-only-not-a-secret", "example", "changeme", "placeholder", "dummy", "not-a-real")


def _iter_files(*roots):
    for root in roots:
        base = Path(REPO_ROOT) / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix not in (".png", ".jpg", ".ico", ".gz", ".zip"):
                yield p


def test_no_inline_secrets_in_deploy_and_infra():
    findings = []
    for path in _iter_files("deploy", "infra"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if PRIVATE_KEY_BLOCK.search(text):
            findings.append(f"{path}: inline private key block")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            m = SECRET_VALUE.search(line)
            if not m:
                continue
            value = m.group(2)
            if value.startswith(("$", "{", "<")) or any(a in value.lower() for a in ALLOWED):
                continue
            if value.lower() in ("true", "false", "null", "none", '""', "''"):
                continue
            findings.append(f"{path}:{lineno}: hardcoded secret-like value {m.group(1)!r}")
    assert not findings, "inline secrets found:\n" + "\n".join(findings)


# ---------------------------------------------------------------------------
# 4. Webhook auth (USSD / IVR telco callbacks)
# ---------------------------------------------------------------------------

USSD_PAYLOAD = {"sessionId": "SAT-SESS-1", "phoneNumber": "+2348012345678", "text": ""}


@pytest.mark.parametrize("channel", ["ussd", "ivr"])
def test_telco_webhook_rejects_missing_secret(channel, monkeypatch):
    monkeypatch.setenv("CITIZEN_PORTAL_TELCO_SECRET", "test-telco-secret")
    mod = load_service_app("mod-citizen-portal")
    with TestClient(mod.create_app()) as c:
        for headers in ({}, {"x-telco-secret": "wrong-secret"}):
            r = c.post(f"/channels/{channel}/callback",
                       params={"state_id": "lagos"}, data=USSD_PAYLOAD, headers=headers)
            assert r.status_code == 403, f"{channel}: webhook accepted auth={headers}"
        # With the correct secret the callback is served.
        r = c.post(f"/channels/{channel}/callback",
                   params={"state_id": "lagos"}, data=USSD_PAYLOAD,
                   headers={"x-telco-secret": "test-telco-secret"})
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("channel", ["ussd", "ivr"])
def test_telco_webhook_fails_closed_when_secret_unset(channel, monkeypatch):
    monkeypatch.delenv("CITIZEN_PORTAL_TELCO_SECRET", raising=False)
    mod = load_service_app("mod-citizen-portal")
    with TestClient(mod.create_app()) as c:
        r = c.post(f"/channels/{channel}/callback",
                   params={"state_id": "lagos"}, data=USSD_PAYLOAD,
                   headers={"x-telco-secret": "anything"})
        assert r.status_code == 403, f"{channel}: webhook open without configured secret"
