"""Tests for deploy/caddy/generate_caddyfile.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_caddyfile as gen  # noqa: E402


def test_generates_blocks_for_all_37_states():
    entries, skipped = gen.load_state_domains()
    assert skipped == [], f"unexpected skips: {skipped}"
    assert len(entries) == 37
    caddyfile = gen.build_caddyfile(entries)
    for tenant, domain in entries:
        assert f"\n{domain} {{" in caddyfile
        assert f"# --- {tenant} ---" in caddyfile
    # Fallback on-demand block present.
    assert "on_demand" in caddyfile
    assert "apisix:9080" in caddyfile
    assert "citizen-pwa:3000" in caddyfile


def test_allowed_domains_json_contains_all_domains():
    entries, _ = gen.load_state_domains()
    payload = json.loads(gen.build_allowed_domains(entries))
    assert payload["domains"] == sorted(d for _, d in entries)
    assert len(payload["domains"]) == 37


def test_deterministic_output():
    entries1, _ = gen.load_state_domains()
    entries2, _ = gen.load_state_domains()
    assert gen.build_caddyfile(entries1) == gen.build_caddyfile(entries2)
    assert gen.build_allowed_domains(entries1) == gen.build_allowed_domains(entries2)
    # Order is sorted by tenant id regardless of filesystem ordering.
    assert [t for t, _ in entries1] == sorted(t for t, _ in entries1)


def test_check_mode_passes_on_committed_files():
    assert gen.main(["--check"]) == 0


def test_check_mode_detects_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "CADDYFILE_PATH", tmp_path / "Caddyfile")
    monkeypatch.setattr(gen, "ALLOWED_PATH", tmp_path / "allowed_domains.json")
    # Missing files -> drift.
    assert gen.main(["--check"]) == 1
    # Write correct files -> OK.
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0
    # Tamper -> drift detected.
    p = tmp_path / "Caddyfile"
    p.write_text(p.read_text() + "# tampered\n")
    assert gen.main(["--check"]) == 1


def test_malformed_domains_skipped(tmp_path):
    states = tmp_path / "states"
    cases = {
        "good": "sos.goodstate.gov.ng",
        "bad_scheme": "https://sos.badstate.gov.ng",
        "bad_underscore": "sos.bad_state.gov.ng",
        "bad_no_tld": "localhost",
        "bad_empty": "",
        "bad_upper_punct": "sos..gov.ng",
    }
    for tenant, domain in cases.items():
        d = states / tenant
        d.mkdir(parents=True)
        (d / "branding.json").write_text(json.dumps({"custom_domain": domain}))
    entries, skipped = gen.load_state_domains(states)
    assert entries == [("good", "sos.goodstate.gov.ng")]
    skipped_tenants = {t for t, _, _ in skipped}
    assert skipped_tenants == {
        "bad_scheme", "bad_underscore", "bad_no_tld", "bad_empty", "bad_upper_punct"
    }


def test_no_valid_domains_errors(tmp_path, monkeypatch):
    entries_dir = tmp_path / "states"
    entries_dir.mkdir()
    assert gen.load_state_domains(entries_dir) == ([], [])
    monkeypatch.setattr(gen, "STATES_DIR", entries_dir)
    assert gen.main(["--check"]) == 2
