"""Shared OIDC JWT authorization middleware for SOS data-plane services.

``require_role(role)`` is a FastAPI dependency factory enforcing that the
caller holds a given stakeholder role. Verification model:

* **Production** (``SOS_AUTH_PROFILE=production``): the ``Authorization:
  Bearer`` token must be an RS256 JWT verified against the state-realm JWKS
  (``SOS_AUTH_JWKS_URL``, optionally templated with ``{state}`` — the path
  ``state_id`` when present). ``exp`` and ``iss`` are verified via
  PyJWT[crypto]. **Fail-closed**: production without a JWKS URL (or without
  PyJWT installed) raises at app boot; a missing/invalid token or a wrong
  role is rejected (401/403).
* **Dev/test (default)**: legacy actor-string passthrough — unauthenticated
  requests and caller-supplied actor strings keep working so existing
  clients/tests are unaffected. A ``DeprecationWarning`` response header
  (``X-Auth-Deprecation``) marks the passthrough. JWT-shaped bearer tokens
  are decoded *unverified* in dev so JWT-shaped callers also pass.

Test seam: ``app.state.auth_verifier`` may hold a callable
``(token: str, state: str | None) -> dict`` returning the verified claims;
when set it takes precedence over the JWKS client in every profile, letting
tests inject a deterministic fixture verifier (wrong role -> 403, etc.).

The dependency returns the actor handle ``"{role}:{sub}"`` so downstream
role checkers that expect the house ``"<role>:<name>"`` style keep working.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from typing import Any, Callable, Optional

from fastapi import HTTPException, Request, Response, status

try:  # import-guarded: PyJWT[crypto] is a production-only dependency
    import jwt
    from jwt import PyJWKClient
except ImportError:  # pragma: no cover - exercised via profile gate
    jwt = None  # type: ignore[assignment]
    PyJWKClient = None  # type: ignore[assignment]

#: JWKS URL template; ``{state}`` is replaced with the path state_id.
DEFAULT_JWKS_URL_TEMPLATE = (
    "http://keycloak:8080/realms/sos-{state}/protocol/openid-connect/certs"
)

DEPRECATION_HEADER = "X-Auth-Deprecation"
DEPRECATION_MSG = (
    "legacy actor-string passthrough; set SOS_AUTH_PROFILE=production with "
    "SOS_AUTH_JWKS_URL to enforce OIDC JWT verification"
)

_jwks_clients: dict[str, Any] = {}
_jwks_lock = threading.Lock()


class AuthConfigurationError(RuntimeError):
    """Fail-closed boot error: production profile without working JWKS config."""


def auth_profile() -> str:
    return os.environ.get("SOS_AUTH_PROFILE", "dev")


def assert_auth_bootable() -> None:
    """Boot-time fail-closed check for wired services.

    With ``SOS_AUTH_PROFILE=production`` a JWKS URL (or PyJWT) absence is a
    hard boot error rather than a runtime surprise.
    """
    if auth_profile() != "production":
        return
    if jwt is None or PyJWKClient is None:
        raise AuthConfigurationError(
            "SOS_AUTH_PROFILE=production requires PyJWT[crypto] (not installed)"
        )
    if not os.environ.get("SOS_AUTH_JWKS_URL"):
        raise AuthConfigurationError(
            "SOS_AUTH_PROFILE=production requires SOS_AUTH_JWKS_URL "
            f"(e.g. {DEFAULT_JWKS_URL_TEMPLATE})"
        )


def _jwks_client(url: str):
    with _jwks_lock:
        client = _jwks_clients.get(url)
        if client is None:
            client = PyJWKClient(url)
            _jwks_clients[url] = client
        return client


def _decode_unverified(token: str) -> dict[str, Any]:
    """Best-effort unverified claims extraction (dev passthrough only)."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return {}


def _bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip() or None
    return None


def _claims_role(claims: dict[str, Any]) -> Optional[str]:
    """Role claim: top-level ``role`` or the first ``roles`` entry."""
    role = claims.get("role")
    if isinstance(role, str) and role:
        return role
    roles = claims.get("roles")
    if isinstance(roles, list) and roles:
        return roles[0]
    return None


def require_role(role: str) -> Callable[[Request], str]:
    """FastAPI dependency enforcing a verified ``role`` on the caller.

    Returns the actor handle ``"{role}:{sub}"``. See module docstring for the
    dev-passthrough and fail-closed semantics.
    """

    def dependency(request: Request, response: Response) -> str:
        state = request.path_params.get("state_id")
        state_value = getattr(state, "value", state) if state is not None else None
        profile = auth_profile()
        verifier = getattr(request.app.state, "auth_verifier", None)
        token = _bearer_token(request)

        # 1. Injected fixture verifier (tests) — enforced in every profile.
        if verifier is not None:
            if token is None:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Bearer token required")
            try:
                claims = verifier(token, state_value)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, f"token verification failed: {exc}")
            if _claims_role(claims) != role:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"role '{role}' required (token carries "
                    f"'{_claims_role(claims)}')")
            return f"{role}:{claims.get('sub', 'unknown')}"

        # 2. Production: JWKS verification, fail-closed.
        if profile == "production":
            assert_auth_bootable()
            if token is None:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Bearer token required")
            url = os.environ["SOS_AUTH_JWKS_URL"]
            if "{state}" in url:
                if not state_value:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "state-scoped JWKS URL but no state_id in path")
                url = url.replace("{state}", str(state_value))
            try:
                signing_key = _jwks_client(url).get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token, signing_key.key, algorithms=["RS256"],
                    options={"require": ["exp", "iss"]},
                )
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, f"JWT verification failed: {exc}")
            if _claims_role(claims) != role:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"role '{role}' required (token carries "
                    f"'{_claims_role(claims)}')")
            return f"{role}:{claims.get('sub', 'unknown')}"

        # 3. Dev/test passthrough: keep legacy actor-string callers working,
        # flagged with a deprecation header.
        response.headers[DEPRECATION_HEADER] = DEPRECATION_MSG
        claims = _decode_unverified(token) if token else {}
        sub = claims.get("sub") or (token.split(".")[0] if token else "anonymous")
        return f"{role}:{sub}"

    return dependency
