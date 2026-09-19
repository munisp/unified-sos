"""Officer provisioning — government officer / agent / auditor onboarding.

Closes the identity-onboarding gap: Keycloak realms are provisioned EMPTY
(``operators/keycloak_realm.py`` pops the dev-template users) and previously
no API existed to onboard officers into them. This module provides:

* a STAKEHOLDER_ROLE catalog mapping officer roles to Keycloak realm groups
  (the groups created by ``keycloak_realm.GROUPS`` plus per-role groups);
* a ``KeycloakAdmin`` seam: deterministic in-memory fixture by default
  (``FixtureKeycloakAdmin``) and a live seam (``RealKeycloakAdmin``) speaking
  to the admin REST API with the same urllib idiom as ``KeycloakOperator``;
* an :class:`OfficerRegistry` driving the lifecycle
  ``INVITED -> ACTIVE -> SUSPENDED -> OFFBOARDED`` with hash-chained audit
  events (``ng.sos.tenant.officer_*``) recorded through the MetadataStore.

Fail-closed: with ``SOS_CP_PROFILE=production`` and no live Keycloak admin
configuration (``KEYCLOAK_ADMIN_URL/USER/PASSWORD``) the registry refuses to
boot (``OfficerConfigurationError``).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .operators.base import OperatorUnavailableError, require_env


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Role catalog — stakeholder roles provisionable via this API, mapped to the
# Keycloak realm groups membership lands in. ``sos-tenant-operators`` /
# ``sos-tenant-auditors`` mirror keycloak_realm.GROUPS; role-specific groups
# follow the ``sos-role-<role>`` convention created on demand.
# ---------------------------------------------------------------------------
STAKEHOLDER_ROLES: dict[str, tuple[str, ...]] = {
    "registry": ("sos-tenant-operators", "sos-role-registry"),
    "surveyor": ("sos-tenant-operators", "sos-role-surveyor"),
    "ministry": ("sos-tenant-operators", "sos-role-ministry"),
    "ag": ("sos-tenant-operators", "sos-role-ag"),
    "governor": ("sos-tenant-admins", "sos-role-governor"),
    "valuer": ("sos-tenant-operators", "sos-role-valuer"),
    "resolver": ("sos-tenant-operators", "sos-role-resolver"),
    "kyc-reviewer": ("sos-tenant-operators", "sos-role-kyc-reviewer"),
    "auditor": ("sos-tenant-auditors", "sos-role-auditor"),
    "dispatch": ("sos-tenant-operators", "sos-role-dispatch"),
    "revenue-officer": ("sos-tenant-operators", "sos-role-revenue-officer"),
    "mda-officer": ("sos-tenant-operators", "sos-role-mda-officer"),
    "agent-supervisor": ("sos-tenant-operators", "sos-role-agent-supervisor"),
}


class OfficerStatus(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"


#: Legal lifecycle transitions. OFFBOARDED is terminal.
_ALLOWED_TRANSITIONS: dict[OfficerStatus, frozenset[OfficerStatus]] = {
    OfficerStatus.INVITED: frozenset({OfficerStatus.ACTIVE, OfficerStatus.OFFBOARDED}),
    OfficerStatus.ACTIVE: frozenset({OfficerStatus.SUSPENDED, OfficerStatus.OFFBOARDED}),
    OfficerStatus.SUSPENDED: frozenset({OfficerStatus.ACTIVE, OfficerStatus.OFFBOARDED}),
    OfficerStatus.OFFBOARDED: frozenset(),
}


class OfficerConfigurationError(RuntimeError):
    """Fail-closed boot error: production profile without Keycloak config."""


class OfficerError(RuntimeError):
    """Illegal transition or unknown officer."""


class OfficerCreate(BaseModel):
    email: str = Field(..., min_length=3)
    display_name: str = Field(..., min_length=1)
    role: str


class Officer(BaseModel):
    officer_id: str
    tenant_id: str
    email: str
    display_name: str
    role: str
    status: OfficerStatus
    keycloak_user_id: str
    groups: list[str]
    history: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# KeycloakAdmin seam
# ---------------------------------------------------------------------------
class KeycloakAdmin(Protocol):
    """User-management seam over a tenant realm."""

    def create_user(self, realm: str, email: str, display_name: str,
                    groups: list[str]) -> tuple[str, str]:
        """Create the user with a temporary credential; returns (user_id, temp_password)."""
        ...

    def add_to_group(self, realm: str, user_id: str, group: str) -> None: ...

    def disable_user(self, realm: str, user_id: str) -> None: ...

    def logout_all(self, realm: str, user_id: str) -> None:
        """Revoke every session for the user (offboarding)."""
        ...


class FixtureKeycloakAdmin:
    """Deterministic in-memory admin seam (default for dev/test/CI)."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def create_user(self, realm: str, email: str, display_name: str,
                    groups: list[str]) -> tuple[str, str]:
        self._seq += 1
        user_id = f"kc-{realm}-{self._seq:04d}"
        temp_password = f"tmp-{secrets.token_hex(4)}"
        self.users[user_id] = {
            "realm": realm, "email": email, "display_name": display_name,
            "enabled": True, "groups": list(groups), "sessions": 1,
            "temp_password": temp_password,
        }
        return user_id, temp_password

    def add_to_group(self, realm: str, user_id: str, group: str) -> None:
        user = self.users[user_id]
        if group not in user["groups"]:
            user["groups"].append(group)

    def disable_user(self, realm: str, user_id: str) -> None:
        self.users[user_id]["enabled"] = False

    def logout_all(self, realm: str, user_id: str) -> None:
        self.users[user_id]["sessions"] = 0


class RealKeycloakAdmin:
    """Live seam over the Keycloak admin REST API (stdlib urllib idiom)."""

    def __init__(self) -> None:
        cfg = require_env(
            "KEYCLOAK_ADMIN_URL", "KEYCLOAK_ADMIN_USER", "KEYCLOAK_ADMIN_PASSWORD"
        )
        self._base = cfg["KEYCLOAK_ADMIN_URL"].rstrip("/")
        self._user = cfg["KEYCLOAK_ADMIN_USER"]
        self._password = cfg["KEYCLOAK_ADMIN_PASSWORD"]

    def _token(self) -> str:
        body = urllib.parse.urlencode({
            "grant_type": "password", "client_id": "admin-cli",
            "username": self._user, "password": self._password,
        }).encode()
        req = urllib.request.Request(
            f"{self._base}/realms/master/protocol/openid-connect/token", data=body)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())["access_token"]
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            raise OfficerError("keycloak admin authentication failed") from exc

    def _request(self, method: str, path: str,
                 payload: dict[str, Any] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"{self._base}{path}", method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {self._token()}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise OfficerError("keycloak admin API unreachable") from exc

    def _group_id(self, realm: str, group: str) -> str:
        status, body = self._request("GET", f"/admin/realms/{realm}/groups")
        if status != 200:
            raise OfficerError(f"keycloak group lookup failed (status {status})")
        for g in json.loads(body):
            if g.get("name") == group:
                return g["id"]
        # Create on demand (upsert semantics).
        status, _ = self._request(
            "POST", f"/admin/realms/{realm}/groups", {"name": group})
        if status not in (201, 409):
            raise OfficerError(f"keycloak group create failed (status {status})")
        return self._group_id(realm, group)

    def create_user(self, realm: str, email: str, display_name: str,
                    groups: list[str]) -> tuple[str, str]:
        temp_password = secrets.token_urlsafe(12)
        first, _, last = display_name.partition(" ")
        status, _ = self._request("POST", f"/admin/realms/{realm}/users", {
            "username": email, "email": email, "enabled": True,
            "firstName": first, "lastName": last or first,
            "credentials": [{"type": "password", "value": temp_password,
                             "temporary": True}],
        })
        if status == 409:
            raise OfficerError(f"keycloak user '{email}' already exists in {realm}")
        if status != 201:
            raise OfficerError(f"keycloak user create failed (status {status})")
        status, body = self._request(
            "GET", f"/admin/realms/{realm}/users?username={urllib.parse.quote(email)}")
        if status != 200 or not json.loads(body):
            raise OfficerError("keycloak user lookup after create failed")
        user_id = json.loads(body)[0]["id"]
        for group in groups:
            self.add_to_group(realm, user_id, group)
        return user_id, temp_password

    def add_to_group(self, realm: str, user_id: str, group: str) -> None:
        gid = self._group_id(realm, group)
        status, _ = self._request(
            "PUT", f"/admin/realms/{realm}/users/{user_id}/groups/{gid}")
        if status not in (204, 409):
            raise OfficerError(f"keycloak group join failed (status {status})")

    def disable_user(self, realm: str, user_id: str) -> None:
        status, _ = self._request(
            "PUT", f"/admin/realms/{realm}/users/{user_id}", {"enabled": False})
        if status != 204:
            raise OfficerError(f"keycloak user disable failed (status {status})")

    def logout_all(self, realm: str, user_id: str) -> None:
        status, _ = self._request(
            "POST", f"/admin/realms/{realm}/users/{user_id}/logout")
        if status not in (204, 404):
            raise OfficerError(f"keycloak logout-all failed (status {status})")


def build_keycloak_admin(env: dict[str, str] | None = None) -> KeycloakAdmin:
    """Fixture seam by default; live seam when Keycloak admin env is present.

    Fail-closed: ``SOS_CP_PROFILE=production`` without the full live config
    raises :class:`OfficerConfigurationError` at boot.
    """
    env = os.environ if env is None else env
    profile = env.get("SOS_CP_PROFILE", "dev")
    configured = all(env.get(v) for v in (
        "KEYCLOAK_ADMIN_URL", "KEYCLOAK_ADMIN_USER", "KEYCLOAK_ADMIN_PASSWORD"))
    if configured:
        return RealKeycloakAdmin()
    if profile == "production":
        raise OfficerConfigurationError(
            "SOS_CP_PROFILE=production requires KEYCLOAK_ADMIN_URL / "
            "KEYCLOAK_ADMIN_USER / KEYCLOAK_ADMIN_PASSWORD for officer provisioning"
        )
    return FixtureKeycloakAdmin()


# ---------------------------------------------------------------------------
# Officer registry — lifecycle + audit
# ---------------------------------------------------------------------------
class OfficerRegistry:
    """Thread-safe in-memory officer registry.

    Every transition is hash-chain audited through the MetadataStore
    (``record_officer_event``) as ``ng.sos.tenant.officer_*`` events on the
    tenant's chain — the same append-only pattern as tenant lifecycle events.
    """

    def __init__(self, store: Any, keycloak: KeycloakAdmin) -> None:
        self._lock = threading.Lock()
        self._officers: dict[str, Officer] = {}
        self._seq = 0
        self._store = store
        self._kc = keycloak

    def _audit(self, event_type: str, tenant_id: str, actor: str,
               detail: dict[str, Any]) -> None:
        self._store.record_officer_event(event_type, tenant_id, actor, detail)

    def invite(self, tenant_id: str, realm: str, req: OfficerCreate,
               actor: str) -> tuple[Officer, str]:
        """Invite an officer: Keycloak user + temp credential, status INVITED."""
        role = req.role.strip()
        if role not in STAKEHOLDER_ROLES:
            raise OfficerError(
                f"unknown role '{role}' (expected one of "
                f"{', '.join(sorted(STAKEHOLDER_ROLES))})")
        groups = list(STAKEHOLDER_ROLES[role])
        with self._lock:
            for officer in self._officers.values():
                if (officer.tenant_id == tenant_id and officer.email == req.email
                        and officer.status != OfficerStatus.OFFBOARDED):
                    raise OfficerError(
                        f"officer '{req.email}' already onboarded for this tenant")
            user_id, temp_password = self._kc.create_user(
                realm, req.email, req.display_name, groups)
            self._seq += 1
            now = _now()
            officer = Officer(
                officer_id=f"ofc-{self._seq:06d}", tenant_id=tenant_id,
                email=req.email, display_name=req.display_name, role=role,
                status=OfficerStatus.INVITED, keycloak_user_id=user_id,
                groups=groups, history=[f"{now} invited"],
                created_at=now, updated_at=now,
            )
            self._officers[officer.officer_id] = officer
            self._audit("ng.sos.tenant.officer_invited", tenant_id, actor, {
                "officer_id": officer.officer_id, "email": officer.email,
                "role": role, "realm": realm,
            })
            return officer, temp_password

    def _transition(self, officer_id: str, target: OfficerStatus, actor: str,
                    action: str) -> Officer:
        with self._lock:
            officer = self._officers.get(officer_id)
            if officer is None:
                raise OfficerError(f"officer '{officer_id}' not found")
            if target not in _ALLOWED_TRANSITIONS[officer.status]:
                raise OfficerError(
                    f"illegal transition {officer.status.value} -> {target.value}")
            if target is OfficerStatus.OFFBOARDED:
                # Disable the account AND revoke every live session.
                tenant = self._store.get_tenant(officer.tenant_id)
                realm = (tenant.provisioned_resources.keycloak_realm
                         if tenant is not None else "sos-unknown")
                self._kc.disable_user(realm, officer.keycloak_user_id)
                self._kc.logout_all(realm, officer.keycloak_user_id)
            previous = officer.status
            officer.status = target
            officer.updated_at = _now()
            officer.history.append(f"{officer.updated_at} {action}")
            self._audit(f"ng.sos.tenant.officer_{action}", officer.tenant_id, actor, {
                "officer_id": officer.officer_id,
                "from": previous.value, "to": target.value,
            })
            return officer

    def activate(self, officer_id: str, actor: str) -> Officer:
        """INVITED -> ACTIVE (first-login / activation confirmation)."""
        return self._transition(officer_id, OfficerStatus.ACTIVE, actor, "activated")

    def suspend(self, officer_id: str, actor: str) -> Officer:
        """ACTIVE -> SUSPENDED."""
        return self._transition(officer_id, OfficerStatus.SUSPENDED, actor, "suspended")

    def offboard(self, officer_id: str, actor: str) -> Officer:
        """Any non-terminal state -> OFFBOARDED (user disabled + sessions revoked)."""
        return self._transition(officer_id, OfficerStatus.OFFBOARDED, actor, "offboarded")

    def get(self, officer_id: str) -> Officer | None:
        return self._officers.get(officer_id)

    def list(self, tenant_id: str, role: str | None = None,
             officer_status: OfficerStatus | None = None) -> list[Officer]:
        officers = [o for o in self._officers.values() if o.tenant_id == tenant_id]
        if role is not None:
            officers = [o for o in officers if o.role == role]
        if officer_status is not None:
            officers = [o for o in officers if o.status == officer_status]
        return sorted(officers, key=lambda o: o.officer_id)
