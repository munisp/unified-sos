"""OpenAPI contract tests (F-053 / Stage 1 FAT).

Blocking, deterministic, fully local:

1. Every ``contracts/openapi/*.yaml`` parses and is a structurally valid
   OpenAPI 3.x document.
2. For each Python service registered in the contract generator
   (``contracts/openapi/generate_from_apps.SERVICES``) the in-process
   ``app.openapi()`` path/method surface must match the committed contract.
   Drift fails the suite — except drift explicitly recorded in
   ``EXPECTED_DRIFT`` below, which must match *exactly* (so the TODO cannot
   silently rot, and new drift never sneaks in under it).

Nothing here is skipped silently: a contract with no owning service, or a
service with no contract, is a hard failure.
"""
from pathlib import Path

import pytest
import yaml

from apputil import REPO_ROOT, load_service_app

import generate_from_apps as gen  # contracts/openapi/generate_from_apps.py

CONTRACTS_DIR = Path(REPO_ROOT) / "contracts" / "openapi"
METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# ---------------------------------------------------------------------------
# Known contract drift, recorded explicitly (never silently passed).
# TODO(F-053): regenerate contracts/openapi/mod-mobility-switch.yaml — the
# P1 Mojaloop/NIBSS escrow seam landed after the contract was curated.
# ---------------------------------------------------------------------------
EXPECTED_DRIFT: dict[str, dict[str, set]] = {
    "mod-mobility-switch": {
        "extra_in_app": {
            ("/mobility/v1/escrow", "post"),
            ("/mobility/v1/escrow/{transfer_id}/abort", "post"),
            ("/mobility/v1/escrow/{transfer_id}/fulfil", "post"),
            ("/mobility/v1/webhooks/nibss/ebills", "post"),
        },
        "missing_in_app": set(),
    },
}

CONTRACT_FILES = sorted(CONTRACTS_DIR.glob("*.yaml"))
GENERATED_SERVICES = sorted(gen.SERVICES)


def _operations(doc: dict) -> set:
    return {
        (path, method)
        for path, item in (doc.get("paths") or {}).items()
        for method in item
        if method in METHODS
    }


@pytest.mark.parametrize("path", CONTRACT_FILES, ids=[p.name for p in CONTRACT_FILES])
def test_openapi_contract_parses_and_is_structurally_valid(path):
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), f"{path.name}: empty or non-mapping YAML"
    assert str(doc.get("openapi", "")).startswith("3."), f"{path.name}: not OpenAPI 3.x"
    assert doc.get("info", {}).get("title"), f"{path.name}: missing info.title"
    assert doc.get("paths"), f"{path.name}: no paths declared"
    for route, item in doc["paths"].items():
        assert isinstance(item, dict) and item, f"{path.name}: {route} has no operations"


def test_every_generated_service_has_a_committed_contract():
    """No service in the generator registry may lack a contract (no silent gaps)."""
    missing = [s for s in GENERATED_SERVICES if not (CONTRACTS_DIR / f"{s}.yaml").exists()]
    assert not missing, f"services without committed OpenAPI contract: {missing}"


def test_expected_drift_entries_reference_real_services():
    for service in EXPECTED_DRIFT:
        assert service in gen.SERVICES, f"EXPECTED_DRIFT entry {service} is stale"


@pytest.mark.parametrize("service", GENERATED_SERVICES)
def test_app_openapi_matches_committed_contract(service):
    committed = yaml.safe_load((CONTRACTS_DIR / f"{service}.yaml").read_text())
    live = gen.build_openapi(service, gen.SERVICES[service])

    extra_in_app = _operations(live) - _operations(committed)
    missing_in_app = _operations(committed) - _operations(live)
    drift = {"extra_in_app": extra_in_app, "missing_in_app": missing_in_app}

    expected = EXPECTED_DRIFT.get(service)
    if expected is not None:
        assert drift == expected, (
            f"{service}: drift changed vs the recorded EXPECTED_DRIFT — "
            f"either regenerate the contract (TODO F-053) or update the list; "
            f"got {drift}"
        )
        return
    assert not extra_in_app and not missing_in_app, (
        f"{service}: app/contract drift — extra in app: {sorted(extra_in_app)}; "
        f"missing from app: {sorted(missing_in_app)}"
    )
