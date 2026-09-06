"""Live registry clients for NIMC / CAC / sanctions federation.

Hashing contract
----------------
These clients NEVER return raw registry payloads and never log or return raw
PII (NIN, RC numbers, personal names, dates of birth). Every verification
method returns a normalized dict whose only identifiers are SHA-256 hashes::

    {"status": <MATCH|MISMATCH|NOT_FOUND|UNAVAILABLE>,
     "confidence": <float 0..1>,
     "<identifier>_sha256": <hex digest>, ...}

The dicts are consumed by :class:`~.registry_adapters.NimcAdapter` /
:class:`~.registry_adapters.CacRegistryAdapter`, which store only the hash of
the already-hashed payload — so raw registry data never crosses the seam.

Fail-closed behaviour
---------------------
* Timeouts, connection errors and 401/403 responses raise
  :class:`~.base.AdapterUnavailableError`.
* Malformed upstream payloads are normalized to ``status=UNAVAILABLE``.
* Each client carries a circuit-breaker counter: after
  ``circuit_threshold`` consecutive transport/auth failures, calls fail
  closed immediately (no network traffic) until a successful call resets it.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, Optional, Protocol

import httpx

from ..domain import sha256_hex
from .base import AdapterUnavailableError

logger = logging.getLogger(__name__)


class SanctionsClient(Protocol):
    """Sanctions screening client seam (stub — no live provider wired yet)."""

    def screen(self, subject_ref: str, name: str) -> Dict[str, object]: ...


class _LiveRegistryClient:
    """Shared transport: OAuth2 client-credentials, retry, circuit breaker."""

    token_path = "/oauth/token"

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.1,
        backoff_cap_s: float = 2.0,
        circuit_threshold: int = 3,
        mtls_cert: Optional[str] = None,
        mtls_key: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not base_url:
            raise AdapterUnavailableError("registry client requires a base_url")
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self.circuit_threshold = circuit_threshold
        self._consecutive_failures = 0
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        cert = (mtls_cert, mtls_key) if mtls_cert and mtls_key else None
        self._http = httpx.Client(
            base_url=self.base_url, timeout=timeout_s, cert=cert, transport=transport
        )

    # -- circuit breaker -----------------------------------------------------
    def _record_failure(self) -> None:
        self._consecutive_failures += 1

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _check_circuit(self) -> None:
        if self._consecutive_failures >= self.circuit_threshold:
            raise AdapterUnavailableError(
                "registry client circuit breaker open after "
                f"{self._consecutive_failures} consecutive failures"
            )

    # -- OAuth2 client credentials -------------------------------------------
    def _bearer_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        try:
            resp = self._http.post(
                self.token_path,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise AdapterUnavailableError(f"token request failed: {type(exc).__name__}")
        if resp.status_code in (401, 403):
            raise AdapterUnavailableError(
                f"registry token endpoint rejected credentials (HTTP {resp.status_code})"
            )
        if resp.status_code >= 400:
            raise AdapterUnavailableError(
                f"registry token endpoint error (HTTP {resp.status_code})"
            )
        try:
            body = resp.json()
            token = body["access_token"]
            expires_in = float(body.get("expires_in", 300))
        except (ValueError, KeyError, TypeError):
            raise AdapterUnavailableError("malformed token response from registry")
        self._token = token
        # Refresh 30s early to avoid mid-call expiry.
        self._token_expires_at = now + max(expires_in - 30.0, 0.0)
        return token

    # -- bounded retry POST ----------------------------------------------------
    def _post_authenticated(self, path: str, payload: Dict[str, object]) -> httpx.Response:
        """POST with bearer token, bounded exponential backoff on 5xx/timeouts."""
        self._check_circuit()
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                token = self._bearer_token()
                resp = self._http.post(
                    path,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout_s,
                )
            except AdapterUnavailableError:
                self._record_failure()
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(
                        min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                    )
                continue
            if resp.status_code in (401, 403):
                self._record_failure()
                raise AdapterUnavailableError(
                    f"registry rejected request (HTTP {resp.status_code}); failing closed"
                )
            if resp.status_code >= 500 and attempt < self.max_retries:
                time.sleep(min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt)))
                continue
            if resp.status_code >= 400:
                self._record_failure()
                raise AdapterUnavailableError(
                    f"registry request failed (HTTP {resp.status_code})"
                )
            self._record_success()
            return resp
        self._record_failure()
        raise AdapterUnavailableError(
            f"registry request failed after {self.max_retries + 1} attempts: "
            f"{type(last_exc).__name__ if last_exc else 'server error'}"
        )

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Optional[Dict[str, object]]:
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            return None
        return body if isinstance(body, dict) else None


def _unavailable_payload(id_field: str, id_hash: str) -> Dict[str, object]:
    return {"status": "UNAVAILABLE", "confidence": 0.0, id_field: id_hash}


class NimcClient(_LiveRegistryClient):
    """Live NIMC identity verification client (OAuth2 + optional mTLS)."""

    verify_path = "/v1/nin/verify"

    def verify_nin(
        self, nin: str, full_name: str, date_of_birth: Optional[str]
    ) -> Dict[str, object]:
        """Verify a NIN against NIMC. Returns a hashed/normalized payload only."""
        nin_hash = sha256_hex(nin)
        resp = self._post_authenticated(
            self.verify_path,
            {"nin": nin, "full_name": full_name, "date_of_birth": date_of_birth},
        )
        body = self._safe_json(resp)
        if body is None or "match" not in body:
            logger.warning("NIMC returned malformed payload; normalizing to UNAVAILABLE")
            return _unavailable_payload("nin_sha256", nin_hash)
        match = body.get("match")
        if match is True:
            status = "MATCH"
        elif match is False:
            status = "MISMATCH"
        else:
            status = "NOT_FOUND"
        try:
            confidence = float(body.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "status": status,
            "confidence": confidence,
            "nin_sha256": nin_hash,
        }


class CacClient(_LiveRegistryClient):
    """Live CAC company lookup client (OAuth2 client-credentials)."""

    lookup_path = "/v1/companies/lookup"

    def lookup_company(self, legal_name: str, rc_number: str) -> Dict[str, object]:
        """Look up a company by legal name + RC number. Hashed payload only."""
        rc_hash = sha256_hex(rc_number)
        resp = self._post_authenticated(
            self.lookup_path, {"legal_name": legal_name, "rc_number": rc_number}
        )
        body = self._safe_json(resp)
        if body is None or "found" not in body:
            logger.warning("CAC returned malformed payload; normalizing to UNAVAILABLE")
            return _unavailable_payload("rc_sha256", rc_hash)
        if body.get("found") is not True:
            status = "NOT_FOUND"
        else:
            name_match = body.get("name_match")
            status = "MATCH" if name_match is not False else "MISMATCH"
        try:
            confidence = float(body.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "status": status,
            "confidence": confidence,
            "rc_sha256": rc_hash,
        }


__all__ = ["NimcClient", "CacClient", "SanctionsClient"]
