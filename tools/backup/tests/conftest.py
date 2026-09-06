"""Shared fixtures for tools/backup tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKUP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKUP_DIR))

import postgres_backup  # noqa: E402


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    """A fake backup directory: two tenant 'dumps' + a manifest that
    postgres_backup.write_manifest produced (real hashing code path)."""
    outdir = tmp_path / "backups"
    outdir.mkdir()
    entries = []
    for tenant in ("ogun", "lagos"):
        schema = postgres_backup.tenant_schema(tenant)
        dump = outdir / f"{schema}{postgres_backup.DUMP_SUFFIX}"
        dump.write_bytes(f"fake pg_dump payload for {tenant}\n".encode() * 3)
        entries.append({
            "tenant": tenant,
            "schema": schema,
            "dump_file": dump.name,
            "bytes": dump.stat().st_size,
            "sha256": postgres_backup.sha256_file(dump),
            "tables": {},
        })
    postgres_backup.write_manifest(entries, outdir)
    return outdir
