"""Policy-pack validation for the control plane.

Schema: contracts/policy-packs/revenue-split.schema.json (JSON Schema draft
2020-12). Guardrails mirror `sosctl policy validate` (tools/sosctl) — the two
implementations are intentionally independent (no cross-directory imports).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

#: Repo-relative schema location (services/control-plane/app/policy.py -> root).
SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts" / "policy-packs" / "revenue-split.schema.json"
)

#: Concessionaire revenue-share ceilings by state class (procurement guardrails).
REVENUE_SHARE_CEILINGS: dict[str, float] = {
    "lagos": 8.0, "ogun": 8.0,
    "osun": 15.0, "benue": 15.0, "nasarawa": 15.0, "taraba": 15.0,
}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def validate_policy_pack(doc: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty when valid)."""
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(load_schema())
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {path}: {err.message}")
    if errors:
        return errors

    rules = doc.get("statutory_split_rules") or []
    instant_sum = sum(
        float(r.get("split_percentage", 0))
        for r in rules
        if isinstance(r, dict) and r.get("deduction_timing") == "INSTANT"
    )
    if instant_sum > 100:
        errors.append(
            f"INSTANT deduction legs sum to {instant_sum:g}% (> 100%); remainder must stay "
            "in the payer clearing account until END_OF_MONTH sweeps"
        )
    for r in rules:
        code = r.get("tigerbeetle_account_code") if isinstance(r, dict) else None
        if isinstance(code, int) and not (1000 <= code <= 9999):
            errors.append(f"tigerbeetle_account_code {code} out of control band 1000-9999")

    ceiling = (doc.get("concession_guardrails") or {}).get("revenue_share_ceiling_pct")
    state = doc.get("tenant_state_id")
    if ceiling is not None and state in REVENUE_SHARE_CEILINGS:
        if float(ceiling) > REVENUE_SHARE_CEILINGS[state]:
            errors.append(
                f"revenue_share_ceiling_pct {ceiling:g}% exceeds the "
                f"{REVENUE_SHARE_CEILINGS[state]:g}% procurement ceiling for state '{state}'"
            )
    return errors
