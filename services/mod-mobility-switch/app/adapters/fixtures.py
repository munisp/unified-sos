"""Deterministic fixture adapters for local development and tests.

FixtureFspiopAdapter / FixtureNibssAdapter mirror the production adapter
interfaces with fully deterministic, in-memory behaviour keyed by
transfer_id / bill_reference (idempotent replays return identical results).
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Mapping

from .fspiop import FspiopAdapter, TransferFulfilment, compute_hmac_signature
from .nibss_ebills import NibssEBillsAdapter, SettlementRow, compute_bill_signature

FIXTURE_SECRET = "fixture-scheme-secret"  # local-only; never a real credential


def _fixture_fulfilment(transfer_id: str) -> str:
    digest = hashlib.sha256(f"fixture-fulfil:{transfer_id}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _fixture_condition(transfer_id: str) -> str:
    digest = hashlib.sha256(f"fixture-condition:{transfer_id}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


class FixtureFspiopAdapter(FspiopAdapter):
    """In-memory FSPIOP peer: prepare/fulfil keyed by transfer_id."""

    def __init__(self, callback_secret: str = FIXTURE_SECRET) -> None:
        super().__init__(enabled=True, base_url="fixture://fspiop",
                         callback_secret=callback_secret)
        self._prepared: dict[str, dict[str, Any]] = {}
        self._fulfilled: dict[str, dict[str, Any]] = {}

    def party_lookup(self, id_type: str, id_value: str, ctx=None) -> dict[str, Any]:
        digest = hashlib.sha256(f"{id_type}:{id_value}".encode("utf-8")).hexdigest()
        return {
            "party": {
                "partyIdInfo": {"partyIdType": id_type, "partyIdentifier": id_value},
                "name": f"FIXTURE PARTY {digest[:8].upper()}",
                "fspId": "fixture-fsp",
            }
        }

    def quote(self, transfer_id: str, amount_kobo: int, payer: str, payee: str,
              ctx=None) -> dict[str, Any]:
        fee = amount_kobo // 100 + 1000  # deterministic 1% + ₦10.00
        return {
            "transactionId": transfer_id,
            "transferAmountMinor": str(amount_kobo),
            "payeeFspFeeMinor": str(fee),
            "condition": _fixture_condition(transfer_id),
        }

    def transfer_prepare(self, transfer_id: str, amount_kobo: int, condition: str,
                         expiration: str, ctx=None) -> dict[str, Any]:
        # Idempotent on transfer_id: replays return the same pending record.
        if transfer_id not in self._prepared:
            self._prepared[transfer_id] = {
                "transferId": transfer_id,
                "amountMinor": str(amount_kobo),
                "condition": condition or _fixture_condition(transfer_id),
                "state": "PENDING",
                "expiration": expiration,
            }
        return dict(self._prepared[transfer_id])

    def transfer_fulfil(self, transfer_id: str, fulfilment: str, ctx=None) -> dict[str, Any]:
        if transfer_id not in self._prepared:
            raise KeyError(f"transfer '{transfer_id}' was never prepared")
        if transfer_id not in self._fulfilled:
            self._fulfilled[transfer_id] = {
                "transferId": transfer_id,
                "fulfilment": fulfilment or _fixture_fulfilment(transfer_id),
                "transferState": "COMMITTED",
                "completedTimestamp": "2026-01-01T00:00:00Z",
            }
        return dict(self._fulfilled[transfer_id])

    def sign_callback(self, body: bytes) -> str:
        """Test/sim helper: produce a valid FSPIOP-Signature for a body."""
        return compute_hmac_signature(self._callback_secret, body)


class FixtureNibssAdapter(NibssEBillsAdapter):
    """In-memory NIBSS e-Bills peer: bills + settlement sheet keyed by ref."""

    def __init__(self, hmac_secret: str = FIXTURE_SECRET) -> None:
        super().__init__(enabled=True, base_url="fixture://nibss",
                         hmac_secret=hmac_secret)
        self._bills: dict[str, dict[str, Any]] = {}

    def create_bill(self, bill_reference: str, amount_kobo: int, payer_ref: str,
                    metadata: dict[str, Any] | None = None, ctx=None) -> dict[str, Any]:
        if bill_reference not in self._bills:
            self._bills[bill_reference] = {
                "billReference": bill_reference,
                "amountMinor": amount_kobo,
                "payerRef": payer_ref,
                "status": "ISSUED",
                "gatewayRef": "NIBSS-FIX-" + hashlib.sha256(
                    bill_reference.encode("utf-8")).hexdigest()[:8].upper(),
            }
        return dict(self._bills[bill_reference])

    def settlement_report(self, settlement_date: str) -> list[SettlementRow]:
        rows = []
        for ref in sorted(self._bills):
            bill = self._bills[ref]
            rows.append(SettlementRow(
                bill_reference=ref,
                amount_kobo=int(bill["amountMinor"]),
                channel="NIP",
                settled_at=f"{settlement_date}T23:59:00Z",
                provider_reference=bill["gatewayRef"],
            ))
        return rows

    def sign_notification(self, body: bytes) -> str:
        """Test/sim helper: produce a valid inbound HMAC signature."""
        return compute_bill_signature(self._hmac_secret, body)


def fixture_fulfilment_for(transfer_id: str) -> TransferFulfilment:
    """Build the deterministic fulfilment a fixture peer would emit."""
    return TransferFulfilment(
        transfer_id=transfer_id,
        transfer_state="COMMITTED",
        fulfilment=_fixture_fulfilment(transfer_id),
        completed_timestamp="2026-01-01T00:00:00Z",
    )
