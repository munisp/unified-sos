"""Domain service: issuance, QR payloads, tracking, levy, warehouse receipts.

QR payload format (compact, checkpoint-scannable, < 10 s verify target):

    SOSWB1.<base64url(json)>.<base64url(hmac-sha256)>

The HMAC key is a service-level signing key (policy-pack/secret-store in
production). The payload contains only the fields a checkpoint needs:
waybill number, vehicle plate, produce type, quantity, destination, levy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Dict, List, Optional

from .models import (
    EWaybill,
    TrackingEvent,
    VerificationResult,
    WarehouseReceipt,
    WaybillStatus,
    utcnow,
)

QR_VERSION = "SOSWB1"


class NotFoundError(KeyError):
    pass


class InvalidTransition(ValueError):
    pass


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


class AgriWaybillService:
    def __init__(self, signing_key: bytes = b"dev-signing-key") -> None:
        self._key = signing_key
        self._waybills: Dict[str, EWaybill] = {}
        self._tracking: Dict[str, List[TrackingEvent]] = {}
        self._receipts: Dict[str, WarehouseReceipt] = {}

    # -- issuance & QR ---------------------------------------------------------

    def issue_waybill(self, waybill: EWaybill) -> EWaybill:
        if waybill.waybill_number in self._waybills:
            raise InvalidTransition(f"waybill {waybill.waybill_number!r} exists")
        waybill.qr_payload = self._make_qr_payload(waybill)
        self._waybills[waybill.waybill_number] = waybill
        self._tracking[waybill.waybill_number] = []
        return waybill

    def _make_qr_payload(self, wb: EWaybill) -> str:
        body = {
            "wb": wb.waybill_number,
            "plate": wb.vehicle_plate,
            "produce": wb.produce_type.value,
            "kg": wb.quantity_kg,
            "dest": wb.destination,
            "levy_kobo": wb.levy_kobo,
        }
        raw = _b64e(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
        sig = _b64e(hmac.new(self._key, raw.encode(), hashlib.sha256).digest())
        return f"{QR_VERSION}.{raw}.{sig}"

    def verify_qr(self, qr_payload: str) -> VerificationResult:
        """Verify a scanned QR payload and return consignment status."""
        try:
            version, raw, sig = qr_payload.split(".")
        except ValueError:
            return VerificationResult(
                waybill_number="", valid=False, detail="malformed payload"
            )
        if version != QR_VERSION:
            return VerificationResult(
                waybill_number="", valid=False, detail=f"unsupported version {version!r}"
            )
        expected = _b64e(hmac.new(self._key, raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return VerificationResult(
                waybill_number="", valid=False, detail="signature mismatch"
            )
        try:
            body = json.loads(_b64d(raw))
        except (ValueError, json.JSONDecodeError):
            return VerificationResult(
                waybill_number="", valid=False, detail="corrupt payload body"
            )
        wb = self._waybills.get(body.get("wb", ""))
        if wb is None:
            return VerificationResult(
                waybill_number=body.get("wb", ""),
                valid=False,
                detail="waybill not registered",
            )
        return VerificationResult(
            waybill_number=wb.waybill_number, valid=True, status=wb.status
        )

    def get_waybill(self, waybill_number: str) -> EWaybill:
        wb = self._waybills.get(waybill_number)
        if wb is None:
            raise NotFoundError(f"waybill {waybill_number!r} not found")
        return wb

    # -- consignment tracking ----------------------------------------------------

    def record_checkpoint(self, event: TrackingEvent) -> TrackingEvent:
        wb = self.get_waybill(event.waybill_number)
        if wb.status in (WaybillStatus.DELIVERED, WaybillStatus.CANCELLED):
            raise InvalidTransition(f"waybill already {wb.status.value.lower()}")
        self._tracking[wb.waybill_number].append(event)
        if wb.status == WaybillStatus.ISSUED:
            wb.status = WaybillStatus.IN_TRANSIT
        return event

    def tracking_trail(self, waybill_number: str) -> List[TrackingEvent]:
        self.get_waybill(waybill_number)
        return list(self._tracking[waybill_number])

    def mark_delivered(self, waybill_number: str) -> EWaybill:
        wb = self.get_waybill(waybill_number)
        if wb.status == WaybillStatus.CANCELLED:
            raise InvalidTransition("cancelled waybill cannot be delivered")
        wb.status = WaybillStatus.DELIVERED
        wb.delivered_at = utcnow()
        return wb

    # -- warehouse receipts --------------------------------------------------------

    def issue_receipt(self, receipt: WarehouseReceipt) -> WarehouseReceipt:
        wb = self.get_waybill(receipt.waybill_number)
        if wb.status != WaybillStatus.DELIVERED:
            raise InvalidTransition(
                "warehouse receipts require a delivered consignment"
            )
        if receipt.produce_type != wb.produce_type:
            raise InvalidTransition("receipt produce type does not match waybill")
        if receipt.quantity_kg > wb.quantity_kg:
            raise InvalidTransition("receipt quantity exceeds consignment quantity")
        if receipt.receipt_id in self._receipts:
            raise InvalidTransition(f"receipt {receipt.receipt_id!r} exists")
        self._receipts[receipt.receipt_id] = receipt
        return receipt

    def get_receipt(self, receipt_id: str) -> WarehouseReceipt:
        rc = self._receipts.get(receipt_id)
        if rc is None:
            raise NotFoundError(f"receipt {receipt_id!r} not found")
        return rc

    def list_receipts(self, warehouse_id: Optional[str] = None) -> List[WarehouseReceipt]:
        receipts = list(self._receipts.values())
        return (
            [r for r in receipts if r.warehouse_id == warehouse_id]
            if warehouse_id
            else receipts
        )
