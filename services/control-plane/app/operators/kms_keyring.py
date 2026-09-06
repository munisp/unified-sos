"""KMS keyring operator — one data-encryption key per tenant, behind a small
backend interface so Vault Transit or a cloud KMS can be plugged in.

Fail-closed: requires KMS_BACKEND=vault plus KMS_VAULT_ADDR / KMS_VAULT_TOKEN.
The Vault backend uses the Transit secrets engine over stdlib urllib (no
hvac dependency).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Protocol

from .base import OperatorUnavailableError, ProvisionError, require_env

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant


class KeyringBackend(Protocol):
    """Minimal key-management interface (key per tenant)."""

    def ensure_key(self, key_name: str) -> None:
        """Create the key if absent (upsert semantics)."""
        ...

    def delete_key(self, key_name: str) -> None: ...


class VaultTransitBackend:
    """HashiCorp Vault Transit engine backend (REST, no hvac dependency)."""

    def __init__(self, addr: str, token: str, mount: str = "transit") -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._mount = mount

    def _request(self, method: str, path: str,
                 payload: dict | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"{self._addr}/v1/{path}", method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"X-Vault-Token": self._token,
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise ProvisionError("vault transit backend unreachable") from exc

    def ensure_key(self, key_name: str) -> None:
        status, _ = self._request(
            "POST", f"{self._mount}/keys/{urllib.parse.quote(key_name)}",
            {"type": "aes256-gcm96"},
        )
        if status not in (200, 201, 204):
            raise ProvisionError(f"vault key create failed (status {status})")

    def delete_key(self, key_name: str) -> None:
        quoted = urllib.parse.quote(key_name)
        # Vault keys must be marked deletable before deletion.
        self._request("POST", f"{self._mount}/keys/{quoted}/config",
                      {"deletion_allowed": True})
        self._request("DELETE", f"{self._mount}/keys/{quoted}")


class KmsOperator:
    name = "kms"

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        if backend is not None:
            self._backend = backend
        else:
            cfg = require_env("KMS_BACKEND")
            if cfg["KMS_BACKEND"] != "vault":
                raise OperatorUnavailableError(
                    f"unsupported KMS_BACKEND '{cfg['KMS_BACKEND']}' (supported: vault)"
                )
            vault = require_env("KMS_VAULT_ADDR", "KMS_VAULT_TOKEN")
            self._backend = VaultTransitBackend(
                vault["KMS_VAULT_ADDR"], vault["KMS_VAULT_TOKEN"]
            )
        self._issued: dict[str, str] = {}  # tenant_id -> key name (for rollback)

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str:
        key_name = f"{resources.kms_keyring}-{tenant.tenant_id}"
        self._backend.ensure_key(key_name)
        self._issued[tenant.tenant_id] = key_name
        return "kms-keyring-issued"

    def decommission(self, tenant_id: str) -> None:
        key_name = self._issued.pop(tenant_id, None)
        if key_name is not None:
            self._backend.delete_key(key_name)
