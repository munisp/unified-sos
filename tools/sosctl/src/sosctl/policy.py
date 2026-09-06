"""Policy-pack validation and staging.

Validates dynamic state policy packs against
``contracts/policy-packs/revenue-split.schema.json`` (JSON Schema draft
2020-12) plus SOS procurement guardrails that are not expressible in schema:

* INSTANT-leg split percentages must sum to <= 100 (remainder stays in the
  payer clearing account until END_OF_MONTH sweeps).
* Concessionaire revenue-share ceilings: 15% agrarian/extractive states,
  8% Lagos/Ogun.
* TigerBeetle account codes must be integers in 1000..9999.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from .states import REVENUE_SHARE_CEILINGS, STATE_TENANT_IDS

DEFAULT_SCHEMA_PATH = Path("contracts/policy-packs/revenue-split.schema.json")


@dataclass
class ValidationResult:
    """Outcome of validating a policy pack."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    document: dict | None = None

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise PolicyValidationError(self.errors)


class PolicyValidationError(Exception):
    """Raised when a policy pack fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("policy validation failed: " + "; ".join(errors))


def load_schema(schema_path: Path) -> dict:
    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"schema not found: {path}")
    return json.loads(path.read_text())


def guardrail_errors(doc: dict) -> list[str]:
    """Non-schema guardrail checks. Returns a list of human-readable errors."""
    errors: list[str] = []

    rules = doc.get("statutory_split_rules") or []
    if isinstance(rules, list):
        # Guardrail 1: INSTANT legs must sum to <= 100.
        instant_sum = sum(
            float(r.get("split_percentage", 0))
            for r in rules
            if isinstance(r, dict) and r.get("deduction_timing") == "INSTANT"
        )
        if instant_sum > 100:
            errors.append(
                f"INSTANT deduction legs sum to {instant_sum:g}% (> 100%); remainder "
                "must stay in the payer clearing account until END_OF_MONTH sweeps"
            )
        # Guardrail 3: account codes in the 1000-9999 control band.
        for r in rules:
            if not isinstance(r, dict):
                continue
            code = r.get("tigerbeetle_account_code")
            if isinstance(code, int) and not (1000 <= code <= 9999):
                errors.append(
                    f"tigerbeetle_account_code {code} out of control band 1000-9999"
                )

    # Guardrail 2: concessionaire revenue-share ceilings by state class.
    guardrails = doc.get("concession_guardrails") or {}
    ceiling = guardrails.get("revenue_share_ceiling_pct")
    state = doc.get("tenant_state_id")
    if ceiling is not None and state in REVENUE_SHARE_CEILINGS:
        max_ceiling = REVENUE_SHARE_CEILINGS[state]
        if float(ceiling) > max_ceiling:
            klass = "Lagos/Ogun" if max_ceiling == 8.0 else "agrarian/extractive"
            errors.append(
                f"revenue_share_ceiling_pct {ceiling:g}% exceeds the {max_ceiling:g}% "
                f"procurement ceiling for {klass} state '{state}'"
            )
    return errors


def validate_policy(doc: dict, schema: dict) -> ValidationResult:
    """Validate a policy pack document against schema + guardrails."""
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {path}: {err.message}")
    if not errors:
        errors.extend(guardrail_errors(doc))
    return ValidationResult(ok=not errors, errors=errors, document=doc)


def validate_policy_file(
    file_path: Path, schema_path: Path = DEFAULT_SCHEMA_PATH
) -> ValidationResult:
    path = Path(file_path)
    if not path.exists():
        return ValidationResult(ok=False, errors=[f"file not found: {path}"])
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return ValidationResult(ok=False, errors=[f"invalid JSON: {exc}"])
    return validate_policy(doc, load_schema(schema_path))


def stage_policy(state: str, doc: dict, out_dir: Path) -> Path:
    """Stage a validated policy pack into the config-style output dir.

    Layout mirrors production ``config/states/<state>/`` so ArgoCD can pick it
    up unchanged. Idempotent: deterministic JSON, skipped when unchanged.
    """
    if state not in STATE_TENANT_IDS:
        raise ValueError(f"unknown state '{state}'")
    dest_dir = Path(out_dir) / state
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "policy-pack.json"
    content = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if not (dest.exists() and dest.read_text() == content):
        dest.write_text(content)
    return dest
