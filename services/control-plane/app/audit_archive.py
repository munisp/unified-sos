"""Immutable audit archive backends (P1 workstream 4).

Every :class:`~app.domain.AuditEvent` appended by the metadata store is
mirrored to an :class:`AuditArchive` — a write-only, tamper-evident sink.
Two backends ship:

  * :class:`LocalFileArchive` — append-only JSONL, one file per tenant chain.
    Deterministic default; used in dev/test and as the source for
    ``sosctl audit verify-chain``.
  * :class:`OpenSearchArchive` — production backend (7-year retention via the
    ISM policy in ``infra/helm/opensearch/``). ``opensearch-py`` is an
    optional import; the backend is selected with ``AUDIT_ARCHIVE=opensearch``
    and **fails closed** (raises :class:`AuditArchiveUnavailableError`) when
    the driver or ``OPENSEARCH_URL`` is missing — a control plane that
    cannot archive audit events must not silently run without them.

Selection via :func:`archive_from_env` (fail-closed on unknown selector).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from .domain import AuditEvent


class AuditArchiveUnavailableError(RuntimeError):
    """Raised when the configured audit archive backend cannot be used.

    Fail-closed idiom (mirrors mod-kyc-kyb ``AdapterUnavailableError``):
    a missing optional dependency or missing configuration is fatal, never
    silently degraded.
    """


class AuditArchive(Protocol):
    """Write-only archive for hash-chained audit events."""

    def append(self, event: AuditEvent) -> None:
        """Durably append one event. Must raise on failure (no swallow)."""
        ...

    def read_all(self, tenant_id: str | None) -> list[dict]:
        """Read back the tenant's chain in append order (verification only)."""
        ...


def _chain_key(tenant_id: str | None) -> str:
    return tenant_id if tenant_id is not None else "_global"


class LocalFileArchive:
    """Append-only JSONL archive (one ``<tenant>.jsonl`` file per chain).

    Deterministic default backend: no external services, byte-for-byte
    reproducible lines (canonical JSON), suitable for chain verification
    after process restarts.
    """

    def __init__(self, root: str | Path = "out/audit") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, tenant_id: str | None) -> Path:
        return self._root / f"{_chain_key(tenant_id)}.jsonl"

    def append(self, event: AuditEvent) -> None:
        line = json.dumps(event.model_dump(), sort_keys=True,
                          separators=(",", ":"), default=str)
        with self._path(event.tenant_id).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self, tenant_id: str | None) -> list[dict]:
        path = self._path(tenant_id)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class OpenSearchArchive:
    """Write-only OpenSearch archive (production, 7-year ISM retention).

    One index per tenant chain (``<index_prefix>-<tenant>``) so retention /
    WORM snapshots apply per tenant. Fail-closed: construction raises
    :class:`AuditArchiveUnavailableError` unless the ``opensearch-py``
    driver is installed and ``OPENSEARCH_URL`` (or explicit ``url``) is set.
    """

    def __init__(self, url: str | None = None, index_prefix: str = "sos-audit") -> None:
        url = url or os.environ.get("OPENSEARCH_URL")
        if not url:
            raise AuditArchiveUnavailableError(
                "OpenSearchArchive requires OPENSEARCH_URL (fail-closed: "
                "refusing to run the audit archive without a configured sink)")
        try:
            from opensearchpy import OpenSearch
        except ImportError as exc:
            raise AuditArchiveUnavailableError(
                "OpenSearchArchive requires the optional 'opensearch-py' "
                "package (pip install opensearch-py) — fail-closed") from exc
        self._client = OpenSearch(url)
        self._index_prefix = index_prefix

    def _index(self, tenant_id: str | None) -> str:
        return f"{self._index_prefix}-{_chain_key(tenant_id)}"

    def append(self, event: AuditEvent) -> None:
        # Write-only: index the immutable document; no update/delete API is
        # exposed. The event_hash is the document id, making replays
        # idempotent and tamper-evident.
        self._client.index(index=self._index(event.tenant_id),
                           id=event.event_hash, body=event.model_dump())

    def read_all(self, tenant_id: str | None) -> list[dict]:
        resp = self._client.search(
            index=self._index(tenant_id),
            body={"size": 10_000, "sort": [{"seq": "asc"}], "query": {"match_all": {}}},
        )
        return [hit["_source"] for hit in resp["hits"]["hits"]]


def archive_from_env() -> AuditArchive:
    """Select the archive backend from the environment (fail-closed).

    ``AUDIT_ARCHIVE``:
      * unset / ``local``      — :class:`LocalFileArchive` at
                                 ``AUDIT_ARCHIVE_PATH`` (default ``out/audit``)
      * ``opensearch``         — :class:`OpenSearchArchive` (requires
                                 ``OPENSEARCH_URL`` + ``opensearch-py``)
      * anything else          — raises (unknown backend: fail closed)
    """

    kind = os.environ.get("AUDIT_ARCHIVE", "local").strip().lower()
    if kind == "local":
        return LocalFileArchive(os.environ.get("AUDIT_ARCHIVE_PATH", "out/audit"))
    if kind == "opensearch":
        return OpenSearchArchive()
    raise AuditArchiveUnavailableError(
        f"unknown AUDIT_ARCHIVE '{kind}' (expected local|opensearch) — fail-closed")
