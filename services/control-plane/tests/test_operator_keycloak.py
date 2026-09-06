"""Tests for the Keycloak realm operator against a fake admin REST API.

The operator speaks stdlib urllib to the Keycloak admin API; these tests run
a local in-process HTTP server implementing just enough of that API to
exercise provision (new + existing realm), scope/group upserts, decommission,
and the fail-closed error paths. No live Keycloak required.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain import MetadataStore, TenantCreate
from app.operators import local_operators
from app.operators.base import OperatorUnavailableError, ProvisionError
from app.operators.keycloak_realm import CLIENT_SCOPES, GROUPS, KeycloakOperator

_ENV = {
    "KEYCLOAK_ADMIN_USER": "admin",
    "KEYCLOAK_ADMIN_PASSWORD": "admin",
}


class _FakeKeycloak(BaseHTTPRequestHandler):
    """Minimal Keycloak admin API: token, realm lookup/import, scopes, groups."""

    realms: dict[str, dict] = {}
    requests: list[tuple[str, str]] = []
    auth_ok: bool = True

    def log_message(self, *args) -> None:  # silence test output
        pass

    def _send(self, code: int, body: dict | None = None) -> None:
        data = json.dumps(body or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).requests.append(("POST", self.path))
        if self.path == "/realms/master/protocol/openid-connect/token":
            if not type(self).auth_ok:
                return self._send(401, {"error": "invalid_grant"})
            return self._send(200, {"access_token": "fake-token"})
        if self.path == "/admin/realms":
            doc = json.loads(body)
            type(self).realms[doc["realm"]] = doc
            return self._send(201)
        if self.path.endswith("/client-scopes") or self.path.endswith("/groups"):
            return self._send(201)
        self._send(404)

    def do_GET(self) -> None:
        type(self).requests.append(("GET", self.path))
        realm = self.path.removeprefix("/admin/realms/")
        if realm in type(self).realms:
            return self._send(200, type(self).realms[realm])
        self._send(404)

    def do_DELETE(self) -> None:
        type(self).requests.append(("DELETE", self.path))
        realm = self.path.removeprefix("/admin/realms/")
        if realm in type(self).realms:
            del type(self).realms[realm]
            return self._send(204)
        self._send(404)


@pytest.fixture()
def fake_keycloak(monkeypatch: pytest.MonkeyPatch):
    _FakeKeycloak.realms = {}
    _FakeKeycloak.requests = []
    _FakeKeycloak.auth_ok = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeKeycloak)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("KEYCLOAK_ADMIN_URL", url)
    yield _FakeKeycloak
    server.shutdown()
    thread.join()


def _tenant():
    store = MetadataStore(operators=local_operators())
    tenant = store.create_tenant(TenantCreate(state="ogun", tier="hybrid"))
    return tenant, tenant.provisioned_resources


def test_provision_imports_realm_scopes_and_groups(fake_keycloak) -> None:
    tenant, resources = _tenant()
    op = KeycloakOperator()
    assert op.provision(tenant, resources) == "keycloak-realm-imported"

    realm = resources.keycloak_realm
    assert realm in fake_keycloak.realms
    doc = fake_keycloak.realms[realm]
    # Template dev users and client secrets are never carried forward.
    assert "users" not in doc
    assert all("secret" not in c for c in doc.get("clients", []))
    assert doc["enabled"] is True

    paths = [p for _, p in fake_keycloak.requests]
    for scope in CLIENT_SCOPES:
        assert f"/admin/realms/{realm}/client-scopes" in paths
    for group in GROUPS:
        assert f"/admin/realms/{realm}/groups" in paths

    # Decommission deletes only the realm this process created.
    op.decommission(tenant.tenant_id)
    assert realm not in fake_keycloak.realms


def test_provision_is_idempotent_for_existing_realm(fake_keycloak) -> None:
    tenant, resources = _tenant()
    fake_keycloak.realms[resources.keycloak_realm] = {"realm": resources.keycloak_realm}
    op = KeycloakOperator()
    assert op.provision(tenant, resources) == "keycloak-realm-imported"
    methods_paths = fake_keycloak.requests
    assert ("POST", "/admin/realms") not in methods_paths  # no re-import


def test_admin_auth_failure_raises_provision_error(fake_keycloak) -> None:
    fake_keycloak.auth_ok = False
    tenant, resources = _tenant()
    op = KeycloakOperator()
    with pytest.raises(ProvisionError, match="authentication failed"):
        op.provision(tenant, resources)


def test_realm_lookup_error_raises_provision_error(fake_keycloak, monkeypatch) -> None:
    tenant, resources = _tenant()
    op = KeycloakOperator()

    def boom(method, path, token, payload=None):
        return 500, b"{}"

    monkeypatch.setattr(op, "_request", boom)
    with pytest.raises(ProvisionError, match="lookup failed"):
        op.provision(tenant, resources)


def test_realm_import_failure_raises_provision_error(fake_keycloak, monkeypatch) -> None:
    tenant, resources = _tenant()
    op = KeycloakOperator()

    def boom(method, path, token, payload=None):
        return (404, b"") if method == "GET" else (500, b"{}")

    monkeypatch.setattr(op, "_request", boom)
    with pytest.raises(ProvisionError, match="import failed"):
        op.provision(tenant, resources)


def test_missing_template_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("KEYCLOAK_ADMIN_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("KEYCLOAK_REALM_TEMPLATE", "/nonexistent/realm.json")
    with pytest.raises(OperatorUnavailableError, match="template not found"):
        KeycloakOperator()


def test_decommission_unknown_tenant_is_noop() -> None:
    op = KeycloakOperator.__new__(KeycloakOperator)  # avoid env/template needs
    op._created = {}
    assert op.decommission("tenant-never-created") is None
