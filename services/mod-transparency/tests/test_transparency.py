"""pytest suite for mod-transparency: structural redaction (no donor/actor
PII keys), tenant isolation + 404 non-enumeration, procurement hash-chain
tamper detection, deterministic in-memory fixtures, fail-closed HTTP seam."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.domain import (
    AUDIT_GENESIS_HASH,
    KNOWN_STATE_IDS,
    ProcurementAuditDigest,
    verify_digest_chain,
)
from app.main import create_app
from app.sources import (
    HttpTransparencySource,
    InMemoryTransparencySource,
    SourceUnavailableError,
)

# Keys that must never appear in any public transparency payload.
FORBIDDEN_PII_KEYS = {
    "donor_ref", "donor_name", "actor_id", "bidder_name", "full_name",
    "nin", "bvn", "phone", "email", "details",
}


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_keys(item)


@pytest.fixture()
def client():
    return TestClient(create_app(InMemoryTransparencySource()))


# --- determinism ------------------------------------------------------------

def test_fixtures_are_deterministic():
    a = InMemoryTransparencySource()
    b = InMemoryTransparencySource()
    for state in KNOWN_STATE_IDS:
        assert a.trust_fund_feed(state) == b.trust_fund_feed(state)
        assert a.escrow_statements(state) == b.escrow_statements(state)
        assert a.procurement_audit(state) == b.procurement_audit(state)


# --- trust-fund feed ---------------------------------------------------------

def test_trust_fund_feed_shape_and_balance(client):
    resp = client.get("/transparency/v1/lagos/trust-fund/feed")
    assert resp.status_code == 200
    feed = resp.json()
    assert feed["state_id"] == "lagos"
    assert len(feed["entries"]) == 3
    donated = sum(e["amount_kobo"] for e in feed["entries"] if e["kind"] == "donation")
    spent = sum(e["amount_kobo"] for e in feed["entries"] if e["kind"] == "disbursement")
    assert feed["balance_kobo"] == donated - spent
    assert feed["head_cursor"] == feed["entries"][-1]["cursor"]
    # cursors form a chain anchored at the genesis hash
    prev = AUDIT_GENESIS_HASH
    for entry in feed["entries"]:
        assert entry["cursor"] != prev
        prev = entry["cursor"]


def test_trust_fund_feed_has_no_pii_keys(client):
    for state in KNOWN_STATE_IDS:
        resp = client.get(f"/transparency/v1/{state}/trust-fund/feed")
        assert resp.status_code == 200
        keys = set(_walk_keys(resp.json()))
        assert keys.isdisjoint(FORBIDDEN_PII_KEYS), keys & FORBIDDEN_PII_KEYS
        for entry in resp.json()["entries"]:
            if entry["kind"] == "donation":
                # pseudonym present, raw donor reference absent
                assert entry["donor_alias_hash"]
                assert len(entry["donor_alias_hash"]) == 64
                assert "donor" not in entry.get("entry_id", "").lower()


def test_donor_alias_is_one_way(client):
    feed = client.get("/transparency/v1/lagos/trust-fund/feed").json()
    aliases = [e["donor_alias_hash"] for e in feed["entries"] if e["kind"] == "donation"]
    assert all(a and a not in ("donor-ref-alpha", "donor-ref-beta") for a in aliases)


# --- escrow statements -------------------------------------------------------

def test_escrow_statements_redacted_and_balanced(client):
    resp = client.get("/transparency/v1/nasarawa/escrow/statements")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows, "nasarawa fixture must have at least one statement"
    for row in rows:
        assert set(_walk_keys(row)).isdisjoint(FORBIDDEN_PII_KEYS)
        assert "contract_id" not in row  # only the pseudonymised hash
        assert row["state_share_kobo"] + row["concessionaire_share_kobo"] == \
            row["gross_collections_kobo"]


# --- procurement audit + tamper detection ------------------------------------

def test_procurement_audit_verifies_clean_chain(client):
    resp = client.get("/transparency/v1/nasarawa/procurement/audit/verify")
    assert resp.status_code == 200
    body = resp.json()
    assert body["chain_valid"] is True
    assert body["entries_checked"] == 3
    assert body["first_invalid_seq"] is None
    assert body["head_hash"]


def test_procurement_audit_feed_has_no_actor_or_details(client):
    resp = client.get("/transparency/v1/nasarawa/procurement/audit")
    assert resp.status_code == 200
    for digest in resp.json():
        assert set(digest).isdisjoint(FORBIDDEN_PII_KEYS)


def test_tampered_chain_is_detected():
    src = InMemoryTransparencySource()
    digests = [d.model_copy() for d in src.procurement_audit("nasarawa")]
    # tamper: rewrite history — change an action mid-chain
    digests[1] = digests[1].model_copy(update={"action": "PROPOSAL_REJECTED"})
    valid, first_invalid = verify_digest_chain(digests)
    assert not valid
    assert first_invalid == digests[1].seq


def test_dropped_entry_is_detected():
    src = InMemoryTransparencySource()
    digests = src.procurement_audit("nasarawa")
    del digests[1]
    valid, first_invalid = verify_digest_chain(digests)
    assert not valid
    assert first_invalid == 3  # seq 3's prev_hash no longer matches


def test_verify_endpoint_reports_tamper(client):
    class TamperedSource(InMemoryTransparencySource):
        def procurement_audit(self, state_id):
            digests = [d.model_copy() for d in super().procurement_audit(state_id)]
            if digests:
                digests[0] = digests[0].model_copy(update={"action": "PROJECT_DELETED"})
            return digests

    tampered = TestClient(create_app(TamperedSource()))
    body = tampered.get("/transparency/v1/nasarawa/procurement/audit/verify").json()
    assert body["chain_valid"] is False
    assert body["first_invalid_seq"] == 1


# --- tenant isolation / non-enumeration --------------------------------------

def test_unknown_state_returns_404_generic(client):
    for path in ("trust-fund/feed", "escrow/statements",
                 "procurement/audit", "procurement/audit/verify"):
        resp = client.get(f"/transparency/v1/atlantis/{path}")
        assert resp.status_code == 404, path
        body = resp.json()
        assert "atlantis" not in str(body)  # no echo, no enumeration hint


def test_empty_state_returns_empty_feed_not_error(client):
    resp = client.get("/transparency/v1/osun/trust-fund/feed")
    assert resp.status_code == 200
    feed = resp.json()
    assert feed["entries"] == []
    assert feed["balance_kobo"] == 0


def test_tenant_feeds_do_not_bleed_across_states(client):
    lagos = client.get("/transparency/v1/lagos/trust-fund/feed").json()
    ogun = client.get("/transparency/v1/ogun/trust-fund/feed").json()
    lagos_ids = {e["entry_id"] for e in lagos["entries"]}
    ogun_ids = {e["entry_id"] for e in ogun["entries"]}
    assert lagos_ids.isdisjoint(ogun_ids)
    assert all(e["entry_id"].startswith("tf-lagos-") for e in lagos["entries"])
    nas_audit = client.get("/transparency/v1/nasarawa/procurement/audit").json()
    tar_audit = client.get("/transparency/v1/taraba/procurement/audit").json()
    assert {d["subject_ref_hash"] for d in nas_audit}.isdisjoint(
        {d["subject_ref_hash"] for d in tar_audit})


# --- health + fail-closed seam -----------------------------------------------

def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["module"] == "mod-transparency"


def test_http_source_fails_closed_without_config(monkeypatch):
    monkeypatch.delenv("POLICE_CAD_BASE_URL", raising=False)
    monkeypatch.delenv("PPP_INVESTMENT_BASE_URL", raising=False)
    with pytest.raises(SourceUnavailableError):
        HttpTransparencySource.from_env()


def test_http_source_unavailable_upstream_maps_to_503():
    src = HttpTransparencySource("http://127.0.0.1:1", "http://127.0.0.1:1",
                                 timeout_seconds=0.2)
    c = TestClient(create_app(src))
    resp = c.get("/transparency/v1/lagos/trust-fund/feed")
    assert resp.status_code == 503


def test_openapi_routes_match_contract():
    spec = create_app(InMemoryTransparencySource()).openapi()
    paths = set(spec["paths"])
    assert paths == {
        "/transparency/v1/{state_id}/trust-fund/feed",
        "/transparency/v1/{state_id}/escrow/statements",
        "/transparency/v1/{state_id}/procurement/audit",
        "/transparency/v1/{state_id}/procurement/audit/verify",
        "/healthz",
    }
