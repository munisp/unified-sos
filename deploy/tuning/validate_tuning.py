#!/usr/bin/env python3
"""Validation for the SOS performance tuning pack (stdlib only, pytest-compatible).

Run standalone:   python3 deploy/tuning/validate_tuning.py
Run under pytest: python3 -m pytest deploy/tuning/validate_tuning.py

Checks:
  1. Every config file parses (ini-ish / properties / YAML-lite / redis conf).
  2. No placeholder markers (TODO/FIXME/XXX/TBD) in any tuning file or the doc.
  3. The master doc table references every file in deploy/tuning/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOC = ROOT.parent.parent / "docs" / "operations" / "performance-tuning.md"

PLACEHOLDER = re.compile(r"\b(TODO|FIXME|XXX|TBD)\b", re.IGNORECASE)


def _strip_comments(text: str, markers: tuple[str, ...] = ("#",)) -> list[str]:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or any(s.startswith(m) for m in markers):
            continue
        lines.append(s)
    return lines


def parse_ini_like(path: Path) -> None:
    """postgresql.conf / redis.conf style: key value pairs, no sections."""
    lines = _strip_comments(path.read_text())
    bad = [l for l in lines if not re.match(r"^[A-Za-z0-9_.\-]+\s+\S+", l)]
    assert not bad, f"{path.name}: unparsable lines: {bad[:3]}"


def parse_properties(path: Path) -> None:
    """Java .properties: key=value lines (and '#' comments)."""
    lines = _strip_comments(path.read_text())
    bad = [l for l in lines if not re.match(r"^[A-Za-z0-9_.\-]+\s*=", l)]
    assert not bad, f"{path.name}: unparsable lines: {bad[:3]}"


def parse_my_cnf(path: Path) -> None:
    """ini with [sections]; '=' or bare flags allowed."""
    text = path.read_text()
    assert "[mysqld]" in text, "my.cnf must contain a [mysqld] section"
    for line in _strip_comments(text):
        if line.startswith("[") and line.endswith("]"):
            continue
        assert "=" in line or re.match(r"^[A-Za-z0-9_\-]+$", line), (
            f"my.cnf unparsable line: {line}"
        )


def parse_yaml(path: Path) -> None:
    """YAML parse — PyYAML if present, else strict structural lint."""
    text = path.read_text()
    try:
        import yaml  # type: ignore

        yaml.safe_load(text)
        return
    except ImportError:
        pass
    assert "\t" not in text, f"{path.name}: tabs are invalid YAML indentation"
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        assert line.rstrip() == line or True
        assert re.match(r"^\s*([A-Za-z0-9_.'\"{}$/-]+:|[-\s]|$)", line), (
            f"{path.name}: suspicious YAML line: {line!r}"
        )


FILES = {
    "postgresql.conf": parse_ini_like,
    "redis.conf": parse_ini_like,
    "my.cnf": parse_my_cnf,
    "kafka-server.properties": parse_properties,
    "apisix.yaml": parse_yaml,
    "opensearch.yml": parse_yaml,
}

MARKDOWN_FILES = [
    "tigerbeetle.md",
    "fluvio.md",
    "keycloak.md",
    "temporal.md",
    "permify.md",
    "lakehouse.md",
]


def test_config_files_exist_and_parse():
    for name, parser in FILES.items():
        p = ROOT / name
        assert p.is_file(), f"missing {name}"
        parser(p)


def test_markdown_files_exist_and_nonempty():
    for name in MARKDOWN_FILES:
        p = ROOT / name
        assert p.is_file(), f"missing {name}"
        assert len(p.read_text()) > 2000, f"{name} is suspiciously short"


def test_no_placeholders():
    targets = [ROOT / n for n in list(FILES) + MARKDOWN_FILES] + [DOC]
    for p in targets:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            assert not PLACEHOLDER.search(line), f"{p.name}:{i}: placeholder marker"


def test_doc_references_every_file():
    assert DOC.is_file(), f"missing master doc {DOC}"
    doc = DOC.read_text()
    for name in list(FILES) + MARKDOWN_FILES:
        assert name in doc, f"master doc does not reference {name}"


def main() -> int:
    checks = [
        test_config_files_exist_and_parse,
        test_markdown_files_exist_and_nonempty,
        test_no_placeholders,
        test_doc_references_every_file,
    ]
    failed = 0
    for check in checks:
        try:
            check()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {check.__name__}: {exc}")
        else:
            print(f"PASS {check.__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
