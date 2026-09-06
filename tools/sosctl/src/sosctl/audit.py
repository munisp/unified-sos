"""Audit hash-chain verification (P1 workstream 4).

Recomputes the SHA-256 hash chain of a tenant's archived audit events
(``services/_shared/hashchain.py``) straight from the archive. Any tamper
— mutation, deletion, re-ordering — breaks the chain and exits non-zero.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Canonical helpers live in services/_shared (repo root = parents[4] from
# tools/sosctl/src/sosctl/audit.py).
_REPO_SERVICES = Path(__file__).resolve().parents[4] / "services"
if (_REPO_SERVICES / "_shared" / "hashchain.py").exists():
    if str(_REPO_SERVICES) not in sys.path:
        sys.path.insert(0, str(_REPO_SERVICES))
    from _shared.hashchain import verify_event_chain
else:  # pragma: no cover - standalone tool checkout
    def verify_event_chain(events):  # type: ignore[no-redef]
        raise RuntimeError("services/_shared/hashchain.py not found — run from a monorepo checkout")


def load_tenant_chain(archive_root: Path, tenant: str) -> list[dict]:
    """Load a tenant chain from a LocalFileArchive JSONL file (append order)."""

    path = Path(archive_root) / f"{tenant}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no audit archive for tenant '{tenant}' at {path}")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def verify_tenant_chain(archive_root: Path, tenant: str) -> list[str]:
    """Return chain-integrity errors for ``tenant`` (empty = intact)."""

    return verify_event_chain(load_tenant_chain(archive_root, tenant))
