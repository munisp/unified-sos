"""pytest suite for mod-citizen-portal citizen channels (USSD/IVR) and
catalog providers: full USSD session walk, invalid input recovery, session
expiry, tenant routing, telco-secret rejection, modules.yaml filtering and
static fallback."""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
import yaml
from fastapi.testclient import TestClient

from app.catalog import ModuleCatalogProvider, StaticCatalogProvider
from app.channels.ivr import TTS_PROMPTS, IvrChannelAdapter
from app.channels.ussd import SESSION_TIMEOUT, UssdChannelAdapter
from app.domain import ChannelSession, hash_msisdn, utcnow
from app.main import create_app
from app.repository import InMemoryCitizenPortalRepository
from app.service import DEFAULT_CATALOG, CitizenPortalService

SECRET = "test-telco-secret"
PHONE = "+2348012345678"
STATE = "lagos"


@pytest.fixture()
def svc():
    return CitizenPortalService(InMemoryCitizenPortalRepository())


@pytest.fixture()
def ussd(svc):
    return UssdChannelAdapter(svc)


@pytest.fixture()
def client(svc, monkeypatch):
    monkeypatch.setenv("CITIZEN_PORTAL_TELCO_SECRET", SECRET)
    return TestClient(create_app(svc.repo))


def _catalog_map(entries):
    return {e.service_code: e for e in entries}


# -- catalog providers --------------------------------------------------------


def test_static_catalog_provider_covers_all_domain_modules():
    entries = StaticCatalogProvider().entries("lagos")
    by_module = {e.module for e in entries}
    assert by_module == {seed[6] for seed in DEFAULT_CATALOG}
    for expected in (
        "mod-mining",
        "mod-agri-waybill",
        "mod-transport-wim",
        "mod-environment",
        "mod-forestry",
        "mod-ppp-investment",
    ):
        assert expected in by_module
    assert all(e.endpoint_hint for e in entries)


def test_module_catalog_provider_filters_by_modules_yaml(tmp_path):
    (tmp_path / "lagos").mkdir()
    (tmp_path / "lagos" / "modules.yaml").write_text(
        yaml.safe_dump(
            {
                "tenant_state_id": "lagos",
                "modules": [{"name": "mod-mining"}, {"name": "mod-forestry"}],
            }
        )
    )
    entries = ModuleCatalogProvider(tmp_path).entries("lagos")
    assert {e.module for e in entries} == {"mod-mining", "mod-forestry"}
    assert {e.service_code for e in entries} == {"MIN-EPERMIT", "FOR-TTP"}


def test_module_catalog_provider_uses_real_repo_config():
    # Lagos config enables mod-mining? If not, assert filtering matches file.
    provider = ModuleCatalogProvider()
    entries = provider.entries("lagos")
    with open(provider._config_root / "lagos" / "modules.yaml") as fh:
        enabled = {m["name"] for m in yaml.safe_load(fh)["modules"]}
    expected = {s[6] for s in DEFAULT_CATALOG if s[6] in enabled}
    assert {e.module for e in entries} == expected


def test_module_catalog_provider_fail_closed_static_fallback(tmp_path):
    # Missing config file -> full static catalog.
    entries = ModuleCatalogProvider(tmp_path).entries("nowhere")
    assert {e.service_code for e in entries} == {s[0] for s in DEFAULT_CATALOG}
    # Unparseable config -> same fallback.
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "modules.yaml").write_text("::: not yaml :::\n- [")
    entries = ModuleCatalogProvider(tmp_path).entries("bad")
    assert {e.service_code for e in entries} == {s[0] for s in DEFAULT_CATALOG}


def test_service_seeds_catalog_through_provider():
    repo = InMemoryCitizenPortalRepository()

    class _MiningOnly:
        def entries(self, state_id):
            return [e for e in StaticCatalogProvider().entries(state_id) if e.module == "mod-mining"]

    svc = CitizenPortalService(repo, catalog_provider=_MiningOnly())
    catalog = svc.ensure_catalog("lagos")
    assert [e.service_code for e in catalog] == ["MIN-EPERMIT"]


# -- USSD state machine --------------------------------------------------------


def test_ussd_full_session_walk_creates_request(ussd, svc):
    h = hash_msisdn(PHONE)
    r1 = ussd.handle_session(STATE, "S1", h, "")
    assert not r1.end_session and "Select a service category" in r1.text
    # Find the MINING category index deterministically from the rendered menu.
    mining_idx = next(
        line.split(".")[0] for line in r1.text.splitlines() if line.endswith("Mining")
    )
    r2 = ussd.handle_session(STATE, "S1", h, mining_idx)
    assert "Mining e-permit" in r2.text and not r2.end_session
    r3 = ussd.handle_session(STATE, "S1", h, "1")
    assert "Confirm request" in r3.text and "MIN" not in r3.text
    assert not r3.end_session
    r4 = ussd.handle_session(STATE, "S1", h, "1")
    assert r4.end_session and "Request submitted" in r4.text
    request_id = next(
        line.split(":")[1].strip() for line in r4.text.splitlines() if line.startswith("Reference:")
    )
    request = svc.get_service_request(request_id, STATE)
    assert request.service_code == "MIN-EPERMIT"
    assert request.form_payload["channel"] == "USSD"
    # Session is closed after submission.
    assert svc.repo.get_channel_session("S1") is None


def test_ussd_invalid_input_recovery(ussd):
    h = hash_msisdn(PHONE)
    ussd.handle_session(STATE, "S2", h, "")
    r = ussd.handle_session(STATE, "S2", h, "99")
    assert "Invalid selection" in r.text and not r.end_session
    r = ussd.handle_session(STATE, "S2", h, "abc")
    assert "Invalid selection" in r.text
    # Session still usable afterwards.
    r = ussd.handle_session(STATE, "S2", h, "1")
    assert "Select a service" in r.text


def test_ussd_back_to_main_menu(ussd):
    h = hash_msisdn(PHONE)
    r1 = ussd.handle_session(STATE, "S3", h, "")
    idx = next(l.split(".")[0] for l in r1.text.splitlines() if l.endswith("Mining"))
    ussd.handle_session(STATE, "S3", h, idx)
    r = ussd.handle_session(STATE, "S3", h, "0")
    assert "Select a service category" in r.text


def test_ussd_session_expiry(ussd, svc):
    h = hash_msisdn(PHONE)
    ussd.handle_session(STATE, "S4", h, "")
    session = svc.repo.get_channel_session("S4")
    session.last_activity = utcnow() - SESSION_TIMEOUT - timedelta(seconds=1)
    svc.repo.save_channel_session(session)
    r = ussd.handle_session(STATE, "S4", h, "")
    # Expired session restarts cleanly at the root menu.
    assert "Select a service category" in r.text
    assert svc.repo.get_channel_session("S4").node == "root"


def test_ussd_tenant_routing_fail_closed(ussd, svc):
    h = hash_msisdn(PHONE)
    ussd.handle_session("lagos", "S5", h, "")
    r = ussd.handle_session("ogun", "S5", h, "1")
    assert r.end_session and "unavailable" in r.text
    assert svc.repo.get_channel_session("S5") is None


def test_ussd_never_stores_raw_msisdn(ussd, svc):
    ussd.handle_session(STATE, "S6", hash_msisdn(PHONE), "")
    session = svc.repo.get_channel_session("S6")
    assert PHONE not in session.model_dump_json()


# -- IVR adapter ----------------------------------------------------------------


def test_ivr_dtmf_grammar_matches_ussd_machine(svc):
    ivr = IvrChannelAdapter(svc, locale="en")
    h = hash_msisdn(PHONE)
    r1 = ivr.handle_session(STATE, "I1", h, "#")
    assert "Select a service category" in r1.text
    idx = next(l.split(".")[0] for l in r1.text.splitlines() if l.endswith("Mining"))
    r2 = ivr.handle_session(STATE, "I1", h, f"*{idx}#")
    assert "Mining e-permit" in r2.text


def test_ivr_tts_prompt_locales():
    for locale in ("en", "yo", "ha", "ig"):
        assert "welcome" in TTS_PROMPTS[locale]
    assert TTS_PROMPTS["yo"]["welcome"].startswith("[yo] ")
    ivr = IvrChannelAdapter(CitizenPortalService(InMemoryCitizenPortalRepository()), locale="ha")
    assert ivr.tts_prompt("welcome", state="Lagos").startswith("[ha] ")
    # Unknown locale fails closed to English.
    ivr = IvrChannelAdapter(CitizenPortalService(InMemoryCitizenPortalRepository()), locale="fr")
    assert ivr.locale == "en"


# -- HTTP webhooks ---------------------------------------------------------------


def _post(client, path, **kwargs):
    return client.post(
        path,
        data={"sessionId": "W1", "phoneNumber": PHONE, "text": ""},
        **kwargs,
    )


def test_ussd_callback_requires_shared_secret(client):
    assert _post(client, "/channels/ussd/callback?state_id=lagos").status_code == 403
    assert (
        _post(
            client,
            "/channels/ussd/callback?state_id=lagos",
            headers={"X-Telco-Secret": "wrong"},
        ).status_code
        == 403
    )


def test_ussd_callback_fail_closed_when_secret_unconfigured(svc, monkeypatch):
    monkeypatch.delenv("CITIZEN_PORTAL_TELCO_SECRET", raising=False)
    client = TestClient(create_app(svc.repo))
    assert (
        _post(
            client,
            "/channels/ussd/callback?state_id=lagos",
            headers={"X-Telco-Secret": SECRET},
        ).status_code
        == 403
    )


def test_ussd_callback_full_walk_over_http(client):
    headers = {"X-Telco-Secret": SECRET}

    def turn(text, session="W2"):
        resp = client.post(
            "/channels/ussd/callback?state_id=lagos",
            data={"sessionId": session, "phoneNumber": PHONE, "text": text},
            headers=headers,
        )
        assert resp.status_code == 200
        return resp.text

    body = turn("")
    assert body.startswith("CON ")
    idx = next(l.split(".")[0] for l in body.splitlines() if l.endswith("Mining"))
    body = turn(idx)
    assert "Mining e-permit" in body
    body = turn("1")
    assert "Confirm request" in body
    body = turn("1")
    assert body.startswith("END ") and "Request submitted" in body


def test_ivr_callback_json_gateway_shape(client):
    resp = client.post(
        "/channels/ivr/callback?state_id=lagos&locale=yo",
        json={"session_id": "I9", "phone_number": PHONE, "text": ""},
        headers={"X-Telco-Secret": SECRET},
    )
    assert resp.status_code == 200
    assert resp.text.startswith("CON [yo] ")


def test_callback_tenant_routing_separate_catalogs(client, svc):
    headers = {"X-Telco-Secret": SECRET}
    client.post(
        "/channels/ussd/callback?state_id=lagos",
        data={"sessionId": "T1", "phoneNumber": PHONE, "text": ""},
        headers=headers,
    )
    assert svc.repo.list_catalog("lagos")
    assert svc.repo.list_catalog("ogun") == []
