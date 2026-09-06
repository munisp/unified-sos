"""NIBSS e-Bills adapter — fail closed unless explicitly enabled.

Seams: create_bill (outbound bill issuance), bill_notification (inbound
payment notification with HMAC-SHA256 signature verification), and
settlement_report (daily settlement sheet with a reconciliation iterator).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping

from .base import (
    DEFAULT_RETRY_POLICY,
    AdapterUnavailableError,
    RequestContext,
    RetryPolicy,
)

SIGNATURE_HEADER = "x-nibss-signature"


def compute_bill_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex of the raw notification body (sim profile)."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SettlementRow:
    """One row of the NIBSS daily settlement sheet."""

    bill_reference: str
    amount_kobo: int
    channel: str  # NIP | USSD | POS | ...
    settled_at: str
    provider_reference: str


@dataclass(frozen=True)
class ReconciliationBreak:
    """A mismatch found while reconciling the settlement sheet."""

    bill_reference: str
    expected_kobo: int
    actual_kobo: int
    reason: str  # MISSING_IN_SHEET | AMOUNT_MISMATCH | UNEXPECTED_IN_SHEET


class NibssEBillsAdapter:
    """NIBSS e-Bills seam. Fails closed unless enabled with endpoint + secret."""

    def __init__(
        self,
        enabled: bool = False,
        base_url: str | None = None,
        client: Any = None,
        hmac_secret: str | None = None,
        retry: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url
        self._client = client
        self._hmac_secret = hmac_secret
        self.retry = retry

    def _require(self) -> None:
        if not self.enabled or (self._client is None and not self.base_url):
            raise AdapterUnavailableError(
                "NIBSS e-Bills adapter unavailable: not enabled or no endpoint configured"
            )

    # --- outbound -----------------------------------------------------------
    def create_bill(
        self,
        bill_reference: str,
        amount_kobo: int,
        payer_ref: str,
        metadata: dict[str, Any] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, Any]:
        """Register a bill with the e-Bills gateway; returns the ack payload."""
        self._require()
        ctx = ctx or RequestContext()
        payload = {
            "billReference": bill_reference,
            "amountMinor": amount_kobo,
            "currency": "NGN",
            "payerRef": payer_ref,
            "metadata": metadata or {},
        }
        headers = {"X-Request-ID": ctx.request_id, "Content-Type": "application/json"}
        return self._client.request(  # pragma: no cover - transport
            "POST", f"{self.base_url or ''}/bills", headers=headers,
            body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        )

    # --- inbound ------------------------------------------------------------
    def verify_notification_signature(
        self, headers: Mapping[str, str], body: bytes
    ) -> bool:
        """Verify the HMAC signature on an inbound bill notification.

        Fail closed: missing secret or header → False.
        """
        signature = None
        for key, value in headers.items():
            if key.lower() == SIGNATURE_HEADER:
                signature = value
                break
        if not signature or not self._hmac_secret:
            return False
        expected = compute_bill_signature(self._hmac_secret, body)
        return hmac.compare_digest(expected, signature)

    # --- settlement sheet ---------------------------------------------------
    def settlement_report(self, settlement_date: str) -> Iterable[SettlementRow]:
        """Fetch the settlement sheet for a date (production: HTTP download)."""
        self._require()
        rows = self._client.request(  # pragma: no cover - transport
            "GET", f"{self.base_url or ''}/settlements/{settlement_date}",
            headers={}, body=None,
        )
        for row in rows.get("rows", []):  # pragma: no cover - transport
            yield SettlementRow(
                bill_reference=row["billReference"],
                amount_kobo=int(row["amountMinor"]),
                channel=row.get("channel", "NIP"),
                settled_at=row.get("settledAt", ""),
                provider_reference=row.get("providerReference", ""),
            )

    @staticmethod
    def reconcile_settlement_sheet(
        expected: Iterable[SettlementRow],
        actual: Iterable[SettlementRow],
    ) -> Iterator[ReconciliationBreak]:
        """Iterate discrepancies between our settlement view and the sheet.

        Yields one ReconciliationBreak per missing, unexpected, or amount-
        mismatched bill reference; an empty iteration means the sheet ties out.
        """
        expected_by_ref = {r.bill_reference: r for r in expected}
        actual_by_ref = {r.bill_reference: r for r in actual}
        for ref in sorted(expected_by_ref):
            if ref not in actual_by_ref:
                yield ReconciliationBreak(ref, expected_by_ref[ref].amount_kobo, 0,
                                          "MISSING_IN_SHEET")
            elif expected_by_ref[ref].amount_kobo != actual_by_ref[ref].amount_kobo:
                yield ReconciliationBreak(ref, expected_by_ref[ref].amount_kobo,
                                          actual_by_ref[ref].amount_kobo, "AMOUNT_MISMATCH")
        for ref in sorted(actual_by_ref):
            if ref not in expected_by_ref:
                yield ReconciliationBreak(ref, 0, actual_by_ref[ref].amount_kobo,
                                          "UNEXPECTED_IN_SHEET")
