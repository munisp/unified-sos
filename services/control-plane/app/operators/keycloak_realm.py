"""Keycloak realm operator — imports a per-tenant realm built from the
deploy/keycloak template (roles, service clients, client scopes, groups) via
the admin REST API (stdlib urllib — no extra dependency).

Fail-closed: requires KEYCLOAK_ADMIN_URL / KEYCLOAK_ADMIN_USER /
KEYCLOAK_ADMIN_PASSWORD.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import OperatorUnavailableError, ProvisionError, require_env

if TYPE_CHECKING:  # pragma: no cover
    from ..domain import ProvisionedResources, Tenant

_DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parents[4] / "deploy" / "keycloak" / "realm-sos-dev.json"
)

#: Client scopes and groups added to every tenant realm on top of the template.
CLIENT_SCOPES = ("sos-tenant-metadata", "sos-audit-read")
GROUPS = ("sos-tenant-admins", "sos-tenant-operators", "sos-tenant-auditors")


class KeycloakOperator:
    name = "keycloak"

    def __init__(self, template_path: str | None = None) -> None:
        cfg = require_env(
            "KEYCLOAK_ADMIN_URL", "KEYCLOAK_ADMIN_USER", "KEYCLOAK_ADMIN_PASSWORD"
        )
        self._base = cfg["KEYCLOAK_ADMIN_URL"].rstrip("/")
        self._user = cfg["KEYCLOAK_ADMIN_USER"]
        self._password = cfg["KEYCLOAK_ADMIN_PASSWORD"]
        path = Path(template_path or os.environ.get("KEYCLOAK_REALM_TEMPLATE", "")
                      or _DEFAULT_TEMPLATE)
        if not path.is_file():
            raise OperatorUnavailableError(
                f"keycloak realm template not found: {path} "
                "(set KEYCLOAK_REALM_TEMPLATE)"
            )
        self._template = json.loads(path.read_text())
        self._created: dict[str, str] = {}  # tenant_id -> realm (for rollback)

    # --- admin REST helpers -------------------------------------------------
    def _token(self) -> str:
        body = urllib.parse.urlencode({
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": self._user,
            "password": self._password,
        }).encode()
        req = urllib.request.Request(
            f"{self._base}/realms/master/protocol/openid-connect/token", data=body
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())["access_token"]
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            raise ProvisionError("keycloak admin authentication failed") from exc

    def _request(self, method: str, path: str, token: str,
                 payload: dict[str, Any] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"{self._base}{path}", method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise ProvisionError("keycloak admin API unreachable") from exc

    # --- operator protocol ---------------------------------------------------
    def _realm_document(self, tenant: "Tenant", resources: "ProvisionedResources") -> dict:
        doc = json.loads(json.dumps(self._template))  # deep copy
        doc["realm"] = resources.keycloak_realm
        doc["displayName"] = f"SOS {tenant.state.title()} ({tenant.tier})"
        doc["enabled"] = True
        # Per-tenant service client: never carry template secrets forward.
        for client in doc.get("clients", []):
            client.pop("secret", None)
        # Template is a dev fixture with dev users — production realms start
        # with no local users (identity comes from the federated IdP).
        doc.pop("users", None)
        return doc

    def provision(self, tenant: "Tenant", resources: "ProvisionedResources") -> str:
        realm = resources.keycloak_realm
        token = self._token()
        status, _ = self._request("GET", f"/admin/realms/{realm}", token)
        if status == 404:
            status, body = self._request(
                "POST", "/admin/realms", token, self._realm_document(tenant, resources)
            )
            if status not in (201, 409):
                raise ProvisionError(f"keycloak realm import failed (status {status})")
        elif status != 200:
            raise ProvisionError(f"keycloak realm lookup failed (status {status})")
        # Client scopes + groups (upsert: 409 already-exists is success).
        for scope in CLIENT_SCOPES:
            status, _ = self._request(
                "POST", f"/admin/realms/{realm}/client-scopes", token,
                {"name": scope, "protocol": "openid-connect"},
            )
            if status not in (201, 409):
                raise ProvisionError(f"keycloak client-scope failed (status {status})")
        for group in GROUPS:
            status, _ = self._request(
                "POST", f"/admin/realms/{realm}/groups", token, {"name": group}
            )
            if status not in (201, 409):
                raise ProvisionError(f"keycloak group failed (status {status})")
        self._created[tenant.tenant_id] = realm
        return "keycloak-realm-imported"

    def decommission(self, tenant_id: str) -> None:
        # Delete only realms this process created (safe against historical
        # tenants sharing a state prefix).
        realm = self._created.pop(tenant_id, None)
        if realm is None:
            return None
        token = self._token()
        self._request("DELETE", f"/admin/realms/{realm}", token)
