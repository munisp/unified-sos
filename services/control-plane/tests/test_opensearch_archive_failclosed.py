"""Fail-closed tests for the OpenSearch audit archive backend (P1 WS4)."""

from __future__ import annotations

import pytest

from app.audit_archive import (
    AuditArchiveUnavailableError,
    LocalFileArchive,
    OpenSearchArchive,
    archive_from_env,
)


def test_opensearch_requires_url(monkeypatch) -> None:
    monkeypatch.delenv("OPENSEARCH_URL", raising=False)
    with pytest.raises(AuditArchiveUnavailableError, match="OPENSEARCH_URL"):
        OpenSearchArchive()


def test_opensearch_requires_driver(monkeypatch) -> None:
    """With a URL but without the optional opensearch-py driver, fail closed."""
    pytest.importorskip("pytest")
    try:
        import opensearchpy  # noqa: F401
        pytest.skip("opensearch-py installed in this environment")
    except ImportError:
        pass
    monkeypatch.setenv("OPENSEARCH_URL", "https://opensearch:9200")
    with pytest.raises(AuditArchiveUnavailableError, match="opensearch-py"):
        OpenSearchArchive()


def test_archive_from_env_opensearch_failclosed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUDIT_ARCHIVE", "opensearch")
    monkeypatch.delenv("OPENSEARCH_URL", raising=False)
    with pytest.raises(AuditArchiveUnavailableError):
        archive_from_env()


def test_archive_from_env_unknown_backend_failclosed(monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_ARCHIVE", "stdout")
    with pytest.raises(AuditArchiveUnavailableError, match="unknown AUDIT_ARCHIVE"):
        archive_from_env()


def test_archive_from_env_defaults_to_local(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AUDIT_ARCHIVE", raising=False)
    monkeypatch.setenv("AUDIT_ARCHIVE_PATH", str(tmp_path))
    archive = archive_from_env()
    assert isinstance(archive, LocalFileArchive)
    assert archive.root == tmp_path
