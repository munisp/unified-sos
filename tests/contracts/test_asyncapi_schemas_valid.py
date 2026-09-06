"""AsyncAPI registry: schemas are valid JSON Schema and match the contracts.

Mirrors the CI schema-compat job: the committed registry under
``contracts/asyncapi/registry/`` must be byte-identical to the deterministic
generator output (``generate.py --check``) and every emitted schema must be a
valid JSON Schema (draft 2020-12).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = REPO_ROOT / "contracts" / "asyncapi" / "registry"
GENERATE = REGISTRY_DIR / "generate.py"
CONTRACTS_DIR = REPO_ROOT / "contracts" / "asyncapi"

SCHEMA_FILES = sorted((REGISTRY_DIR / "schemas").glob("*.json"))


def test_registry_matches_generator_output():
    """Committed registry is byte-identical to generator output (--check)."""
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr


def test_every_channel_has_a_topic_and_schema():
    topics = json.loads((REGISTRY_DIR / "topics.json").read_text())
    by_channel = {}
    for entry in topics["channels"]:
        by_channel.setdefault(entry["channel"], entry)
        assert entry["topic"] == entry["channel"]
        assert (REGISTRY_DIR / entry["schema"]).exists()

    for contract in sorted(CONTRACTS_DIR.glob("*.yaml")):
        doc = next(
            d for d in yaml.safe_load_all(contract.read_text()) if d and "asyncapi" in d
        )
        for channel in doc.get("channels") or {}:
            assert channel in by_channel, f"{contract.name}: channel {channel} unmapped"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=[p.name for p in SCHEMA_FILES])
def test_schema_file_is_valid_json_schema(path):
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(f"/{path.name}")


def test_generator_is_byte_idempotent(tmp_path):
    """Two consecutive generations produce identical bytes."""
    sys.path.insert(0, str(REGISTRY_DIR))
    import generate

    first = generate.build_registry(CONTRACTS_DIR)
    second = generate.build_registry(CONTRACTS_DIR)
    assert first == second
