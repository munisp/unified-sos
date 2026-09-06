"""Tests for `sosctl schema publish|check-compat` (fail-closed config)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from sosctl.cli import app
from sosctl import schema_registry

runner = CliRunner()


def test_check_compat_fails_closed_without_registry_url(monkeypatch) -> None:
    monkeypatch.delenv("SCHEMA_REGISTRY_URL", raising=False)
    result = runner.invoke(app, ["schema", "check-compat"])
    assert result.exit_code == 2
    assert "SCHEMA_REGISTRY_URL" in result.output


def test_publish_fails_closed_without_registry_url(monkeypatch) -> None:
    monkeypatch.delenv("SCHEMA_REGISTRY_URL", raising=False)
    result = runner.invoke(app, ["schema", "publish"])
    assert result.exit_code == 2
    assert "SCHEMA_REGISTRY_URL" in result.output


def test_unknown_registry_type_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("SCHEMA_REGISTRY_URL", "http://localhost:8081")
    monkeypatch.setenv("SCHEMA_REGISTRY_TYPE", "bogus")
    with pytest.raises(schema_registry.SchemaRegistryConfigError):
        schema_registry.check_compatibility()


def test_load_committed_schemas() -> None:
    schemas = schema_registry.load_committed_schemas()
    assert schemas, "expected generated schemas to be committed"
    subjects = [s for s, _ in schemas]
    assert "ng.sos.mining.consignment_dispatched" in subjects
    for _, schema in schemas:
        assert schema["$schema"].endswith("2020-12/schema")
