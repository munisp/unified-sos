"""Boot fail-closed matrix for CONTROL_PLANE_OPERATORS=live.

Live mode must refuse to start and name exactly which configuration is
missing. Operator construction with full config requires the optional
dependencies (kubernetes, asyncpg, boto3) — those cases are skip-gated when
the libraries are not installed.
"""

from __future__ import annotations

import importlib

import pytest

from app.main import build_operators
from app.operators.base import OPERATOR_ORDER, OperatorUnavailableError

_LIVE_ENV = {
    "KUBECONFIG": "/tmp/kubeconfig",
    "CP_PG_DSN": "postgresql://localhost/controlplane",
    "KEYCLOAK_ADMIN_URL": "http://localhost:8080",
    "KEYCLOAK_ADMIN_USER": "admin",
    "KEYCLOAK_ADMIN_PASSWORD": "admin",
    "S3_ENDPOINT_URL": "http://localhost:9000",
    "S3_ACCESS_KEY_ID": "minio",
    "S3_SECRET_ACCESS_KEY": "minio",
    "KMS_BACKEND": "vault",
    "KMS_VAULT_ADDR": "http://localhost:8200",
    "KMS_VAULT_TOKEN": "dev-token",
}


def _set_live_env(monkeypatch: pytest.MonkeyPatch, drop: list[str]) -> None:
    monkeypatch.setenv("CONTROL_PLANE_OPERATORS", "live")
    for key, value in _LIVE_ENV.items():
        if key not in drop:
            monkeypatch.setenv(key, value)


def test_default_mode_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_PLANE_OPERATORS", raising=False)
    operators = build_operators()
    assert list(operators) == list(OPERATOR_ORDER)


def test_invalid_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_OPERATORS", "bogus")
    with pytest.raises(OperatorUnavailableError, match="CONTROL_PLANE_OPERATORS"):
        build_operators()


def test_live_mode_with_no_config_lists_everything_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTROL_PLANE_OPERATORS", "live")
    for key in _LIVE_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(OperatorUnavailableError) as excinfo:
        build_operators()
    message = str(excinfo.value)
    assert "KUBECONFIG or K8S_IN_CLUSTER=true" in message
    for key in ("CP_PG_DSN", "KEYCLOAK_ADMIN_URL", "KEYCLOAK_ADMIN_USER",
                "KEYCLOAK_ADMIN_PASSWORD", "S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY", "KMS_BACKEND", "KMS_VAULT_ADDR",
                "KMS_VAULT_TOKEN"):
        assert key in message


@pytest.mark.parametrize(
    "dropped,missing_text",
    [
        (["CP_PG_DSN"], "CP_PG_DSN"),
        (["KEYCLOAK_ADMIN_PASSWORD"], "KEYCLOAK_ADMIN_PASSWORD"),
        (["S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID"], "S3_ACCESS_KEY_ID"),
        (["KMS_VAULT_TOKEN"], "KMS_VAULT_TOKEN"),
        (["KUBECONFIG"], "KUBECONFIG or K8S_IN_CLUSTER=true"),
    ],
)
def test_live_mode_names_each_missing_var(monkeypatch: pytest.MonkeyPatch,
                                          dropped: list[str],
                                          missing_text: str) -> None:
    _set_live_env(monkeypatch, drop=dropped)
    with pytest.raises(OperatorUnavailableError) as excinfo:
        build_operators()
    message = str(excinfo.value)
    assert missing_text in message
    # Configured vars are not reported as missing.
    configured = [k for k in _LIVE_ENV if k not in dropped]
    for key in configured:
        assert key not in message


def test_live_mode_accepts_in_cluster_instead_of_kubeconfig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_live_env(monkeypatch, drop=["KUBECONFIG"])
    monkeypatch.setenv("K8S_IN_CLUSTER", "true")
    pytest.importorskip("kubernetes")
    pytest.importorskip("asyncpg")
    pytest.importorskip("boto3")
    try:
        build_operators()
    except OperatorUnavailableError as exc:
        # In-cluster config load fails outside a cluster; that is acceptable
        # here — the assertion is that config validation passed.
        assert "missing required configuration" not in str(exc)


@pytest.mark.parametrize(
    "module,classname,kwargs,dep",
    [
        ("app.operators.k8s_namespace", "K8sNamespaceOperator",
         {"kubeconfig": "/tmp/x"}, "kubernetes"),
        ("app.operators.postgres_schema", "PostgresOperator",
         {"dsn": "postgresql://x"}, "asyncpg"),
        ("app.operators.s3_bucket", "S3Operator", {}, "boto3"),
    ],
)
def test_live_operators_construct_with_config(module: str, classname: str,
                                              kwargs: dict, dep: str,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip(dep)
    if dep == "kubernetes":
        # Construction must not require a reachable cluster: stub config load.
        import kubernetes.config

        monkeypatch.setattr(kubernetes.config, "load_kube_config",
                            lambda *a, **k: None)
    for key, value in _LIVE_ENV.items():
        monkeypatch.setenv(key, value)
    cls = getattr(importlib.import_module(module), classname)
    op = cls(**kwargs)
    assert op.name in OPERATOR_ORDER


@pytest.mark.parametrize(
    "module,classname,kwargs",
    [
        ("app.operators.k8s_namespace", "K8sNamespaceOperator", {}),
        ("app.operators.postgres_schema", "PostgresOperator", {}),
        ("app.operators.s3_bucket", "S3Operator", {}),
    ],
)
def test_live_operators_fail_closed_without_config(module: str, classname: str,
                                                   kwargs: dict,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _LIVE_ENV:
        monkeypatch.delenv(key, raising=False)
    cls = getattr(importlib.import_module(module), classname)
    with pytest.raises(OperatorUnavailableError, match="missing|not installed"):
        cls(**kwargs)


def test_kms_operator_fails_closed_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.operators.kms_keyring import KmsOperator

    monkeypatch.delenv("KMS_BACKEND", raising=False)
    with pytest.raises(OperatorUnavailableError, match="KMS_BACKEND"):
        KmsOperator()
    monkeypatch.setenv("KMS_BACKEND", "vault")
    monkeypatch.delenv("KMS_VAULT_TOKEN", raising=False)
    with pytest.raises(OperatorUnavailableError, match="KMS_VAULT_TOKEN"):
        KmsOperator()


def test_kms_operator_with_fake_backend() -> None:
    from app.operators.kms_keyring import KmsOperator

    class FakeBackend:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def ensure_key(self, key_name: str) -> None:
            if key_name not in self.keys:
                self.keys.append(key_name)

        def delete_key(self, key_name: str) -> None:
            self.keys.remove(key_name)

    from app.domain import TenantCreate

    backend = FakeBackend()
    store = __import__("app.domain", fromlist=["MetadataStore"]).MetadataStore()
    tenant = store.create_tenant(TenantCreate(state="ogun", tier="shared"))
    op = KmsOperator(backend=backend)
    assert op.provision(tenant, tenant.provisioned_resources) == "kms-keyring-issued"
    key = f"sos-ogun-keyring-{tenant.tenant_id}"
    assert backend.keys == [key]
    op.provision(tenant, tenant.provisioned_resources)  # upsert: no duplicate
    assert backend.keys == [key]
    op.decommission(tenant.tenant_id)
    assert backend.keys == []
