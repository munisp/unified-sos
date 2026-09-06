"""Identity federation clients: per-state Keycloak token exchange + NIN claim verification.

Fail-closed rules
-----------------
* Default is the deterministic :class:`FixtureFederationClient`.
* ``live`` mode exchanges OAuth2 client-credentials against a per-state
  Keycloak realm and verifies the NIN claim against the state identity
  federation endpoint. Missing env vars fail boot; timeouts / 401s raise
  :class:`FederationUnavailableError` (fail closed at call time).
* No raw PII (NIN) is logged or returned; results are booleans plus a
  SHA-256 claim hash only.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Dict, Optional, Protocol

logger = logging.getLogger(__name__)

# Env vars required for IDENTITY_FEDERATION_MODE=live.
FEDERATION_LIVE_ENV_VARS = (
    "KEYCLOAK_BASE_URL",
    "KEYCLOAK_CLIENT_ID",
    "KEYCLOAK_CLIENT_SECRET",
)


class FederationUnavailableError(RuntimeError):
    """Raised when the identity federation seam must fail closed."""


class IdentityFederationClient(Protocol):
    """Per-state identity federation seam (Keycloak realm + NIN claim check)."""

    def verify_nin_claim(self, state_id: str, nin: str, claim: str) -> Dict[str, object]:
        """Verify a NIN claim for a state tenant.

        Returns ``{"attested": bool, "nin_sha256": str}`` — never raw NIN.
        """
        ...


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


class FixtureFederationClient:
    """Deterministic default federation client (no network)."""

    def verify_nin_claim(self, state_id: str, nin: str, claim: str) -> Dict[str, object]:
        # Deterministic: attests everything except the sentinel "deny" claim
        # used by tests; preserves pre-federation default behaviour.
        attested = claim.strip().lower() != "deny"
        return {"attested": attested, "nin_sha256": _sha256(nin), "source": "fixture"}


class KeycloakFederationClient:
    """Live federation client: client-credentials token per state realm."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.1,
        circuit_threshold: int = 3,
        transport=None,
    ):
        import httpx

        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.circuit_threshold = circuit_threshold
        self._consecutive_failures = 0
        self._tokens: Dict[str, tuple] = {}  # realm -> (token, expires_at)
        self._httpx = httpx
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def _token(self, state_id: str) -> str:
        now = time.monotonic()
        cached = self._tokens.get(state_id)
        if cached and now < cached[1]:
            return cached[0]
        url = f"{self.base_url}/realms/{state_id}/protocol/openid-connect/token"
        try:
            resp = self._http.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=self.timeout_s,
            )
        except (self._httpx.TimeoutException, self._httpx.TransportError) as exc:
            raise FederationUnavailableError(f"token exchange failed: {type(exc).__name__}")
        if resp.status_code in (401, 403):
            raise FederationUnavailableError(
                f"Keycloak rejected federation credentials (HTTP {resp.status_code})"
            )
        if resp.status_code >= 400:
            raise FederationUnavailableError(
                f"Keycloak token endpoint error (HTTP {resp.status_code})"
            )
        try:
            body = resp.json()
            token = body["access_token"]
            expires_in = float(body.get("expires_in", 300))
        except (ValueError, KeyError, TypeError):
            raise FederationUnavailableError("malformed Keycloak token response")
        self._tokens[state_id] = (token, now + max(expires_in - 30.0, 0.0))
        return token

    def verify_nin_claim(self, state_id: str, nin: str, claim: str) -> Dict[str, object]:
        if self._consecutive_failures >= self.circuit_threshold:
            raise FederationUnavailableError(
                "federation client circuit breaker open after "
                f"{self._consecutive_failures} consecutive failures"
            )
        url = f"{self.base_url}/federation/{state_id}/nin/verify-claim"
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._http.post(
                    url,
                    json={"nin": nin, "claim": claim},
                    headers={"Authorization": f"Bearer {self._token(state_id)}"},
                    timeout=self.timeout_s,
                )
            except FederationUnavailableError:
                self._consecutive_failures += 1
                raise
            except (self._httpx.TimeoutException, self._httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_base_s * (2 ** attempt))
                continue
            if resp.status_code in (401, 403):
                self._consecutive_failures += 1
                raise FederationUnavailableError(
                    f"federation rejected request (HTTP {resp.status_code}); failing closed"
                )
            if resp.status_code >= 500 and attempt < self.max_retries:
                time.sleep(self.backoff_base_s * (2 ** attempt))
                continue
            if resp.status_code >= 400:
                self._consecutive_failures += 1
                raise FederationUnavailableError(
                    f"federation request failed (HTTP {resp.status_code})"
                )
            try:
                body = resp.json()
                attested = bool(body["attested"])
            except (ValueError, KeyError, TypeError):
                self._consecutive_failures += 1
                raise FederationUnavailableError("malformed federation response")
            self._consecutive_failures = 0
            return {"attested": attested, "nin_sha256": _sha256(nin), "source": "live"}
        self._consecutive_failures += 1
        raise FederationUnavailableError(
            f"federation request failed after {self.max_retries + 1} attempts: "
            f"{type(last_exc).__name__ if last_exc else 'server error'}"
        )


def build_federation_client(env: Optional[dict] = None) -> IdentityFederationClient:
    """Build a federation client from ``IDENTITY_FEDERATION_MODE``.

    ``fixture`` (default) is deterministic; ``live`` fails closed at boot by
    raising :class:`RuntimeError` listing every missing env var.
    """
    env = os.environ if env is None else env
    mode = env.get("IDENTITY_FEDERATION_MODE", "fixture")
    if mode == "fixture":
        return FixtureFederationClient()
    if mode != "live":
        raise RuntimeError(
            f"unknown IDENTITY_FEDERATION_MODE {mode!r}; expected 'fixture' or 'live'"
        )
    missing = [var for var in FEDERATION_LIVE_ENV_VARS if not env.get(var)]
    if missing:
        raise RuntimeError(
            "IDENTITY_FEDERATION_MODE=live but missing required env vars: "
            + ", ".join(missing)
        )
    return KeycloakFederationClient(
        base_url=env["KEYCLOAK_BASE_URL"],
        client_id=env["KEYCLOAK_CLIENT_ID"],
        client_secret=env["KEYCLOAK_CLIENT_SECRET"],
        timeout_s=float(env.get("FEDERATION_TIMEOUT_S", "10")),
    )


__all__ = [
    "FederationUnavailableError",
    "IdentityFederationClient",
    "FixtureFederationClient",
    "KeycloakFederationClient",
    "build_federation_client",
]
