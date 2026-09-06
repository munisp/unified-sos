"""MinIO object-store adapter — optional import, fail closed when unavailable."""
from __future__ import annotations

import os
from typing import Optional

from .base import AdapterUnavailableError

try:  # optional dependency
    from minio import Minio  # type: ignore

    _MINIO_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    Minio = None  # type: ignore
    _MINIO_AVAILABLE = False


def _is_production() -> bool:
    return os.environ.get("SOS_ENV", "local").lower() in ("prod", "production", "staging")


class MinioObjectStoreAdapter:
    """Production object-store adapter backed by MinIO/S3.

    Fails closed: raises AdapterUnavailableError when the ``minio`` package
    is not installed, or when running in a production-like environment
    without explicit endpoint configuration.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        secure: bool = True,
        enabled: bool = True,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("MINIO_ENDPOINT")
        self.access_key = access_key or os.environ.get("MINIO_ACCESS_KEY")
        self.secret_key = secret_key or os.environ.get("MINIO_SECRET_KEY")
        self.secure = secure
        self.enabled = enabled
        self._client = None
        if not enabled:
            return
        if not _MINIO_AVAILABLE:
            return
        if not (self.endpoint and self.access_key and self.secret_key):
            if _is_production():
                return
            return
        self._client = Minio(  # pragma: no cover - requires live MinIO
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def _require_client(self):
        if self._client is None:
            raise AdapterUnavailableError(
                "MinIO adapter unavailable: dependency not installed or endpoint "
                "not configured (required in production)"
            )
        return self._client

    def put(self, bucket: str, key: str, data: bytes) -> str:
        import io

        client = self._require_client()
        client.put_object(  # pragma: no cover
            bucket, key.lstrip("/"), io.BytesIO(data), length=len(data)
        )
        return key

    def get(self, bucket: str, key: str) -> bytes:
        client = self._require_client()
        resp = client.get_object(bucket, key.lstrip("/"))  # pragma: no cover
        try:  # pragma: no cover
            return resp.read()
        finally:  # pragma: no cover
            resp.close()
            resp.release_conn()

    def presign(self, bucket: str, key: str, expires_seconds: int = 3600) -> str:
        from datetime import timedelta

        client = self._require_client()
        return client.presigned_get_object(  # pragma: no cover
            bucket, key.lstrip("/"), expires=timedelta(seconds=expires_seconds)
        )
