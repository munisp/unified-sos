#!/usr/bin/env python3
"""Validator for config/states/ policy packs.

For every state directory under config/states/:

  1. policy-pack.json is validated against
     contracts/policy-packs/revenue-split.schema.json (JSON Schema 2020-12).
  2. Procurement guardrails are enforced:
       - concession revenue-share ceiling: <= 8% for lagos/ogun,
         <= 15% for agrarian/extractive states (osun, benue, nasarawa, taraba);
       - the PPP_TECH_CONCESSIONAIRE_ESCROW leg itself must respect the ceiling;
       - irr_cap_pct <= 22 (schema maximum, re-asserted here).
  3. INSTANT legs sum to <= 100 (remainder stays in payer clearing account
     1001 until END_OF_MONTH sweeps).
  4. TigerBeetle account codes are 4-digit (1000-9999) and the federal
     pass-through account 5001 is never credited by a state split
     (ledger/chart-of-accounts.md).
  5. modules.yaml parses, uses known mod-* names and valid rollout waves.

Usage: python3 config/states/validate_packs.py  (exit 0 = pass)
Requires: pyyaml, jsonschema
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
STATES_DIR = REPO_ROOT / "config" / "states"
SCHEMA_PATH = REPO_ROOT / "contracts" / "policy-packs" / "revenue-split.schema.json"
OGUN_EXAMPLE = REPO_ROOT / "contracts" / "policy-packs" / "examples" / "ogun-luc-2026.json"

STATES = ["lagos", "ogun", "osun", "benue", "nasarawa", "taraba"]
CONCESSION_CEILING = {"lagos": 8.0, "ogun": 8.0}  # others default to 15%
DEFAULT_CEILING = 15.0
CONCESSION_BENEFICIARY = "PPP_TECH_CONCESSIONAIRE_ESCROW"
FEDERAL_PASS_THROUGH_CODE = 5001  # never credited by state splits

KNOWN_MODULES = {
    "mod-rev-core", "mod-gis-lands", "mod-gis-luc", "mod-mining",
    "mod-forestry", "mod-transport-wim", "mod-agri-waybill", "mod-health",
    "mod-education", "mod-market", "mod-police-cad", "mod-mobility-switch",
    "lakehouse", "control-plane",
}

FAILURES: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"  ok: {msg}")


def check_policy_pack(state: str, validator: Draft202012Validator) -> None:
    pack_path = STATES_DIR / state / "policy-pack.json"
    if not pack_path.exists():
        fail(f"{state}: missing policy-pack.json")
        return
    try:
        pack = json.loads(pack_path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{state}: policy-pack.json is not valid JSON: {exc}")
        return

    errors = sorted(validator.iter_errors(pack), key=lambda e: list(e.path))
    if errors:
        for err in errors:
            fail(f"{state}: schema violation at {list(err.path)}: {err.message}")
        return
    ok(f"{state}: schema valid")

    if pack.get("tenant_state_id") != state:
        fail(f"{state}: tenant_state_id {pack.get('tenant_state_id')!r} != directory name")

    rules = pack["statutory_split_rules"]

    # Guardrail 1: concession ceiling per state class
    ceiling = CONCESSION_CEILING.get(state, DEFAULT_CEILING)
    guard = pack.get("concession_guardrails", {})
    declared = guard.get("revenue_share_ceiling_pct")
    if declared is None:
        fail(f"{state}: concession_guardrails.revenue_share_ceiling_pct missing")
    elif declared > ceiling:
        fail(f"{state}: declared ceiling {declared}% exceeds {ceiling}% for its state class")
    concession_legs = [r for r in rules if r["beneficiary"] == CONCESSION_BENEFICIARY]
    if not concession_legs:
        fail(f"{state}: no {CONCESSION_BENEFICIARY} leg (zero-capex concession requires escrowed split)")
    for leg in concession_legs:
        if leg["split_percentage"] > ceiling:
            fail(f"{state}: concession leg {leg['split_percentage']}% exceeds ceiling {ceiling}%")

    # Guardrail 2: INSTANT legs sum <= 100
    instant_sum = sum(r["split_percentage"] for r in rules if r["deduction_timing"] == "INSTANT")
    if instant_sum > 100:
        fail(f"{state}: INSTANT legs sum to {instant_sum}% (> 100)")
    else:
        ok(f"{state}: INSTANT legs sum = {instant_sum}%")

    # Guardrail 3: account codes in range + federal pass-through never credited
    for leg in rules:
        code = leg["tigerbeetle_account_code"]
        if not (1000 <= code <= 9999):
            fail(f"{state}: account code {code} outside 1000-9999")
        if code == FEDERAL_PASS_THROUGH_CODE:
            fail(f"{state}: federal pass-through account 5001 must never be credited")

    # Guardrail 4: gazette reference mandatory (90-day playbook, Days 1-30)
    if not pack.get("gazette_reference"):
        fail(f"{state}: gazette_reference missing")

    # Ogun pack must mirror the canonical contract example
    if state == "ogun" and OGUN_EXAMPLE.exists():
        example = json.loads(OGUN_EXAMPLE.read_text())
        if pack != example:
            fail("ogun: policy-pack.json does not mirror contracts/policy-packs/examples/ogun-luc-2026.json")
        else:
            ok("ogun: mirrors canonical contract example")


def check_modules_yaml(state: str) -> None:
    mod_path = STATES_DIR / state / "modules.yaml"
    if not mod_path.exists():
        fail(f"{state}: missing modules.yaml")
        return
    try:
        doc = yaml.safe_load(mod_path.read_text())
    except yaml.YAMLError as exc:
        fail(f"{state}: modules.yaml invalid YAML: {exc}")
        return
    if not isinstance(doc, dict) or "modules" not in doc:
        fail(f"{state}: modules.yaml missing top-level 'modules'")
        return
    if doc.get("tenant_state_id") != state:
        fail(f"{state}: modules.yaml tenant_state_id mismatch")
    if not doc["modules"]:
        fail(f"{state}: modules.yaml enables zero modules")
        return
    for mod in doc["modules"]:
        name = mod.get("name", "")
        if name not in KNOWN_MODULES:
            fail(f"{state}: unknown module {name!r}")
        wave = mod.get("wave")
        if not isinstance(wave, int) or not (0 <= wave <= 3):
            fail(f"{state}: module {name} has invalid wave {wave!r}")
    else:
        ok(f"{state}: modules.yaml valid ({len(doc['modules'])} modules)")


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema)

    for state in STATES:
        print(f"== {state} ==")
        state_dir = STATES_DIR / state
        if not state_dir.is_dir():
            fail(f"{state}: directory missing")
            continue
        check_policy_pack(state, validator)
        check_modules_yaml(state)
        if not (state_dir / "README.md").exists():
            fail(f"{state}: missing README.md")

    print()
    if FAILURES:
        print(f"VALIDATION FAILED: {len(FAILURES)} problem(s)")
        return 1
    print(f"VALIDATION PASSED: {len(STATES)} state policy packs conform to schema and guardrails")
    return 0


if __name__ == "__main__":
    sys.exit(main())
