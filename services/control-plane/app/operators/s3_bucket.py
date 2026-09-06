"""S3 bucket operator — versioned, SSE-encrypted, tenant-scoped bucket via
boto3 (MinIO-compatible through S3_ENDPOINT_URL).

Fail-closed: requires the optional ``boto3`` dependency and
S3_ENDPOINT_URL / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY. The credentials
themselves must come from a secret store; the env vars here are references
injected by the platform (never commit real values).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import OperatorUnavailableError, ProvisionError, require_env

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant

try:  # optional dependency
    import boto3
    from botocore.exceptions import ClientError

    _BOTO_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[assignment,misc]
    _BOTO_AVAILABLE = False


class S3Operator:
    name = "s3"

    def __init__(self, kms_key_id: str | None = None) -> None:
        if not _BOTO_AVAILABLE:
            raise OperatorUnavailableError(
                "boto3 is not installed (pip install boto3)"
            )
        cfg = require_env("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg["S3_ENDPOINT_URL"],
            aws_access_key_id=cfg["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=cfg["S3_SECRET_ACCESS_KEY"],
        )
        # SSE-KMS key reference (from the KMS operator's platform keyring).
        self._kms_key_id = kms_key_id or ""
        self._created: dict[str, str] = {}  # tenant_id -> bucket (for rollback)

    def _bucket_policy(self, bucket: str, tenant: "Tenant") -> str:
        import json

        return json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "TenantScopedAccess",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam:::role/sos-tenant-{tenant.state}"},
                "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            }],
        })

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str:
        bucket = resources.s3_bucket
        try:
            self._client.create_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "") \
                if hasattr(exc, "response") else ""
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise ProvisionError("s3 bucket create failed") from exc
        try:
            self._client.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
            encryption: dict = {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "aws:kms" if self._kms_key_id else "AES256",
                    **({"KMSMasterKeyID": self._kms_key_id} if self._kms_key_id else {}),
                }}]
            }
            self._client.put_bucket_encryption(
                Bucket=bucket, ServerSideEncryptionConfiguration=encryption
            )
            self._client.put_bucket_policy(
                Bucket=bucket, Policy=self._bucket_policy(bucket, tenant)
            )
        except ClientError as exc:
            raise ProvisionError("s3 bucket hardening failed") from exc
        self._created[tenant.tenant_id] = bucket
        return "storage-provisioned"

    def decommission(self, tenant_id: str) -> None:
        bucket = self._created.pop(tenant_id, None)
        if bucket is None:
            return None
        try:  # best-effort: empty versioned bucket then delete
            paginator = self._client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                objects = [
                    {"Key": o["Key"], "VersionId": o["VersionId"]}
                    for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
                ]
                if objects:
                    self._client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            self._client.delete_bucket(Bucket=bucket)
        except ClientError:  # pragma: no cover - environment dependent
            pass
