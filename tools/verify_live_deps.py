#!/usr/bin/env python3
"""verify_live_deps.py — Stage 7.A live-dependency compile/verification gate.

Purpose
-------
Close the "reference-only" credibility gap for optional production ("live")
adapter dependencies:

1. IMPORT: every adapter module is imported with its live dependencies
   installed (per constraints-live.txt), proving the pinned versions actually
   satisfy the adapter code — not just a doc reference.
2. FAIL-CLOSED: every live adapter constructor/entry point is asserted to
   refuse operation when its environment configuration is absent, so a
   misconfigured deploy can never silently fall back to an implicit
   endpoint/credential.

Usage
-----
    pip install -r constraints-live.txt   # plus the repo's base test deps
    python3 tools/verify_live_deps.py

Exit code 0 = all adapters imported and fail closed as required.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Env vars that would otherwise configure a live backend; cleared so every
# check below exercises the fail-closed path.
LIVE_ENV_VARS = [
    "GEOSPATIAL_POSTGIS_DSN",
    "GEOSPATIAL_LAKEHOUSE_URI",
    "OPENSEARCH_URL",
    "EVENT_KAFKA_BOOTSTRAP",
    "KUBECONFIG",
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "PADDLEOCR_MODEL_DIR",
]

failures: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - report, keep going
        failures.append(f"{label}: {exc!r}")
        print(f"FAIL  {label}: {exc!r}")
    else:
        print(f"OK    {label}")


def expect_raise(label: str, exc_types: tuple, fn) -> None:
    def run() -> None:
        try:
            fn()
        except exc_types:
            return
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"raised unexpected {type(exc).__name__}: {exc}") from exc
        raise AssertionError("did not fail closed (no exception raised)")

    check(label, run)


def purge_app_modules() -> None:
    """Drop imported `app*` modules so the next service's `app` package wins."""
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]


def push_path(rel: str) -> None:
    sys.path.insert(0, str(REPO_ROOT / rel))


# --------------------------------------------------------------------------
# 0. Clear live configuration so constructors must fail closed.
# --------------------------------------------------------------------------
for var in LIVE_ENV_VARS:
    os.environ.pop(var, None)
os.environ.pop("KYC_KYB_MODE", None)
os.environ.pop("SOS_ENV", None)

# --------------------------------------------------------------------------
# 1. Pinned live dependencies import (compile verification for Python).
# --------------------------------------------------------------------------
def _imports():
    import aiokafka  # noqa: F401
    import asyncpg  # noqa: F401
    import boto3  # noqa: F401
    import deltalake  # noqa: F401
    import httpx  # noqa: F401
    import kubernetes  # noqa: F401
    import minio  # noqa: F401
    import opensearchpy  # noqa: F401
    import psycopg  # noqa: F401
    import temporalio  # noqa: F401


check("pinned live deps import (constraints-live.txt union)", _imports)

# --------------------------------------------------------------------------
# 2. services/mod-geospatial — PostGIS adapter (psycopg)
# --------------------------------------------------------------------------
purge_app_modules()
push_path("services/mod-geospatial")
from app.adapters.postgis_adapter import PostGISConnectionFactory  # noqa: E402
from app.domain import AdapterUnavailableError as GeoAdapterUnavailable  # noqa: E402

expect_raise(
    "mod-geospatial PostGISConnectionFactory fails closed without DSN",
    (GeoAdapterUnavailable,),
    PostGISConnectionFactory,
)

# --------------------------------------------------------------------------
# 3. services/control-plane — K8s / S3 / OpenSearch adapters
# --------------------------------------------------------------------------
purge_app_modules()
push_path("services/control-plane")
from app.audit_archive import AuditArchiveUnavailableError, OpenSearchArchive  # noqa: E402
from app.operators.base import OperatorUnavailableError  # noqa: E402
from app.operators.k8s_namespace import K8sNamespaceOperator  # noqa: E402
from app.operators.s3_bucket import S3Operator  # noqa: E402

expect_raise(
    "control-plane K8sNamespaceOperator fails closed without kubeconfig/cluster",
    (OperatorUnavailableError,),
    K8sNamespaceOperator,
)
expect_raise(
    "control-plane S3Operator fails closed without S3_* env",
    (OperatorUnavailableError,),
    S3Operator,
)
expect_raise(
    "control-plane OpenSearchArchive fails closed without OPENSEARCH_URL",
    (AuditArchiveUnavailableError,),
    OpenSearchArchive,
)

# --------------------------------------------------------------------------
# 4. services/_shared/eventbus — KafkaEventBus (aiokafka)
# --------------------------------------------------------------------------
push_path("services/_shared")
from eventbus import EventBusConfigError, KafkaEventBus  # noqa: E402

expect_raise(
    "_shared KafkaEventBus fails closed without EVENT_KAFKA_BOOTSTRAP",
    (EventBusConfigError,),
    KafkaEventBus,
)

# --------------------------------------------------------------------------
# 5. packages/document-ai — MinIO adapter + PaddleOCR engine
# --------------------------------------------------------------------------
push_path("packages/document-ai")
from document_ai.base import AdapterUnavailableError as DocAiUnavailable  # noqa: E402
from document_ai.minio_adapter import MinioObjectStoreAdapter  # noqa: E402
from document_ai.paddleocr_engine import PaddleOcrEngine  # noqa: E402

expect_raise(
    "document-ai MinioObjectStoreAdapter fails closed without MINIO_* env",
    (DocAiUnavailable,),
    lambda: MinioObjectStoreAdapter()._require_client(),
)
expect_raise(
    "document-ai PaddleOcrEngine fails closed without engine/model config",
    (DocAiUnavailable,),
    lambda: PaddleOcrEngine().extract("s3://bucket/doc.png"),
)

# --------------------------------------------------------------------------
# 6. services/mod-kyc-kyb — live registry clients (httpx)
# --------------------------------------------------------------------------
purge_app_modules()
push_path("services/mod-kyc-kyb")
from app.adapters.base import AdapterUnavailableError as KycAdapterUnavailable  # noqa: E402
from app.adapters.registry_clients import _LiveRegistryClient  # noqa: E402

expect_raise(
    "mod-kyc-kyb live registry client fails closed without base_url",
    (KycAdapterUnavailable,),
    lambda: _LiveRegistryClient(base_url="", client_id="", client_secret=""),
)

# --------------------------------------------------------------------------
print()
if failures:
    print(f"verify_live_deps: {len(failures)} check(s) FAILED")
    sys.exit(1)
print("verify_live_deps: all live adapters import with pinned deps and fail closed")
