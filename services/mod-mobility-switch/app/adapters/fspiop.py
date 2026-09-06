"""Mojaloop FSPIOP v1.1 adapter — fail closed unless explicitly enabled.

Outbound client protocol: party_lookup / quote / transfer_prepare /
transfer_fulfil. Inbound callback protection: FSPIOP-Signature header
verification (HMAC-SHA256 sim profile; production swaps in a JWS verifier
via the `jws_verifier` hook).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .base import (
    DEFAULT_RETRY_POLICY,
    AdapterUnavailableError,
    RequestContext,
    RetryPolicy,
)

FSPIOP_CONTENT_TYPE_TRANSFERS = "application/vnd.interoperability.transfers+json;version=1.1"
FSPIOP_CONTENT_TYPE_QUOTES = "application/vnd.interoperability.quotes+json;version=1.1"
FSPIOP_CONTENT_TYPE_PARTIES = "application/vnd.interoperability.parties+json;version=1.1"
FSPIOP_ACCEPT = "application/vnd.interoperability+json;version=1.1"


def _utc_http_date() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def compute_hmac_signature(secret: str, body: bytes) -> str:
    """Sim-profile FSPIOP-Signature: 'sha256=' + HMAC-SHA256 hex of the body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@dataclass(frozen=True)
class TransferFulfilment:
    """FSPIOP PUT /transfers/{id} fulfilment payload (callback direction)."""

    transfer_id: str
    transfer_state: str  # COMMITTED | ABORTED
    fulfilment: str | None = None
    completed_timestamp: str | None = None
    extension_list: dict[str, Any] | None = None

    @property
    def committed(self) -> bool:
        return self.transfer_state.upper() == "COMMITTED"


class FspiopAdapter:
    """Mojaloop FSPIOP v1.1 client. Fails closed unless enabled with a peer.

    Parameters
    ----------
    enabled:
        Master switch; when False every operation raises
        AdapterUnavailableError (deterministic local default).
    base_url / client:
        Scheme peer endpoint or an injected transport client with
        ``request(method, path, headers=..., body=...)``.
    callback_secret:
        Shared HMAC secret used to verify inbound FSPIOP-Signature headers
        on callbacks (sim profile). Never logged or committed.
    jws_signer / jws_verifier:
        Optional production JWS hooks (sign/verify callables) replacing the
        HMAC sim profile.
    """

    def __init__(
        self,
        enabled: bool = False,
        base_url: str | None = None,
        client: Any = None,
        fspiop_source: str = "sos-switch",
        callback_secret: str | None = None,
        jws_signer: Callable[[bytes], str] | None = None,
        jws_verifier: Callable[[bytes, str], bool] | None = None,
        retry: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url
        self._client = client
        self.fspiop_source = fspiop_source
        self._callback_secret = callback_secret
        self._jws_signer = jws_signer
        self._jws_verifier = jws_verifier
        self.retry = retry

    # --- fail-closed guard -------------------------------------------------
    def _require(self) -> None:
        if not self.enabled or (self._client is None and not self.base_url):
            raise AdapterUnavailableError(
                "FSPIOP adapter unavailable: not enabled or no scheme peer configured"
            )

    # --- header construction ----------------------------------------------
    def _headers(
        self,
        content_type: str,
        destination: str | None,
        ctx: RequestContext,
        body: bytes | None,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": content_type,
            "Accept": FSPIOP_ACCEPT,
            "Date": _utc_http_date(),
            "FSPIOP-Source": self.fspiop_source,
            "X-Request-ID": ctx.request_id,
        }
        if destination:
            headers["FSPIOP-Destination"] = destination
        if body is not None and self._jws_signer is not None:
            headers["FSPIOP-Signature"] = self._jws_signer(body)
        return headers

    def _call(
        self,
        method: str,
        path: str,
        content_type: str,
        payload: dict[str, Any] | None,
        destination: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        self._require()
        ctx = ctx or RequestContext()
        body = json.dumps(payload, sort_keys=True).encode("utf-8") if payload is not None else None
        headers = self._headers(content_type, destination, ctx, body)
        last_exc: Exception | None = None
        for attempt in range(1, self.retry.attempts + 1):
            try:
                return self._client.request(  # pragma: no cover - transport
                    method, f"{self.base_url or ''}{path}", headers=headers, body=body
                )
            except AdapterUnavailableError:
                raise
            except Exception as exc:  # pragma: no cover - transport
                last_exc = exc
                if attempt < self.retry.attempts:
                    self.retry.sleep_for(attempt)
        raise AdapterUnavailableError(  # pragma: no cover - transport
            f"FSPIOP call {method} {path} failed after {self.retry.attempts} attempts: {last_exc}"
        )

    # --- client protocol ----------------------------------------------------
    def party_lookup(
        self, id_type: str, id_value: str, ctx: RequestContext | None = None
    ) -> dict[str, Any]:
        """GET /parties/{Type}/{ID} — resolve a payee alias (opaque, non-PII)."""
        return self._call("GET", f"/parties/{id_type}/{id_value}",
                          FSPIOP_CONTENT_TYPE_PARTIES, None, ctx=ctx)

    def quote(
        self,
        transfer_id: str,
        amount_kobo: int,
        payer: str,
        payee: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        """POST /quotes — fee/commission quote for a transfer."""
        payload = {
            "transactionId": transfer_id,
            "amount": {"currency": "NGN", "amountMinor": str(amount_kobo)},
            "payer": payer,
            "payee": payee,
        }
        return self._call("POST", "/quotes", FSPIOP_CONTENT_TYPE_QUOTES,
                          payload, destination=payee, ctx=ctx)

    def transfer_prepare(
        self,
        transfer_id: str,
        amount_kobo: int,
        condition: str,
        expiration: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        """POST /transfers — prepare (ledger pending/escrow) a transfer."""
        payload = {
            "transferId": transfer_id,
            "amount": {"currency": "NGN", "amountMinor": str(amount_kobo)},
            "condition": condition,
            "expiration": expiration,
        }
        return self._call("POST", "/transfers", FSPIOP_CONTENT_TYPE_TRANSFERS,
                          payload, ctx=ctx)

    def transfer_fulfil(
        self,
        transfer_id: str,
        fulfilment: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        """PUT /transfers/{id} — fulfil (post) a prepared transfer."""
        payload = {
            "fulfilment": fulfilment,
            "transferState": "COMMITTED",
            "completedTimestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return self._call("PUT", f"/transfers/{transfer_id}",
                          FSPIOP_CONTENT_TYPE_TRANSFERS, payload, ctx=ctx)

    # --- inbound callback protection ----------------------------------------
    def verify_inbound_signature(
        self, headers: Mapping[str, str], body: bytes
    ) -> bool:
        """Verify the FSPIOP-Signature header on an inbound callback.

        Fail closed: any missing/invalid material returns False (never raises
        AdapterUnavailableError — a callback we cannot authenticate is simply
        rejected). Production JWS: supply `jws_verifier`.
        """
        signature = None
        for key, value in headers.items():
            if key.lower() == "fspiop-signature":
                signature = value
                break
        if not signature:
            return False
        if self._jws_verifier is not None:
            return bool(self._jws_verifier(body, signature))
        if not self._callback_secret:
            return False
        expected = compute_hmac_signature(self._callback_secret, body)
        return hmac.compare_digest(expected, signature)
