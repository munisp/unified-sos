"""Payload models for edge-buffered records.

Field names and semantics are aligned with the AsyncAPI contracts in
``contracts/asyncapi/platform-events.yaml`` and
``contracts/asyncapi/mining-events.yaml`` so that a synced batch can be
forwarded onto Kafka/Fluvio topics without re-mapping.

Monetary amounts are integer kobo (NGN * 100) — never floats.
"""
from __future__ import annotations

import base64
import enum
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecordKind(str, enum.Enum):
    """Kinds of records the edge daemon can buffer."""

    REVENUE_TICKET = "revenue_ticket"
    E_WAYBILL = "e_waybill"


class RevenueTicketPayload(BaseModel):
    """Signed revenue/tax receipt issued at a POS terminal or checkpoint.

    Maps to the settlement/envelope fields used by
    ``ng.sos.payments.*`` channels (bill_reference, kobo amounts) and to the
    market stallage ticket schema consumed by ``mod-market``.
    """

    kind: Literal[RecordKind.REVENUE_TICKET] = RecordKind.REVENUE_TICKET
    state_id: str
    bill_reference: str
    payer_id: str = Field(description="Trader/TIN/vehicle identifier being charged")
    levy_code: str = Field(
        description="Ledger transfer code, e.g. 110 mineral levy, 130 stallage (ledger/chart-of-accounts.md)"
    )
    amount_kobo: int = Field(ge=0)
    collector_id: str = Field(description="Agent / POS operator ID")
    location: Optional[str] = None
    issued_at: datetime = Field(default_factory=utcnow)


class EWaybillPayload(BaseModel):
    """Produce e-waybill issued offline at a produce checkpoint.

    Consumed by ``services/mod-agri-waybill``; shares envelope conventions
    (state_id, kobo amounts, timestamp) with the AsyncAPI contracts.
    """

    kind: Literal[RecordKind.E_WAYBILL] = RecordKind.E_WAYBILL
    state_id: str
    waybill_number: str
    consignor_id: str
    consignee_id: str
    produce_type: str
    quantity_kg: float = Field(ge=0)
    vehicle_plate: str
    origin: str
    destination: str
    levy_kobo: int = Field(ge=0)
    issued_at: datetime = Field(default_factory=utcnow)


Payload = Annotated[
    Union[RevenueTicketPayload, EWaybillPayload], Field(discriminator="kind")
]


class SignedRecord(BaseModel):
    """An outbox record: payload + device provenance + signature.

    The tuple ``(device_id, sequence)`` is the idempotent dedupe key enforced
    server-side by the gateway.
    """

    device_id: str
    sequence: int = Field(ge=1, description="Monotonic per-device sequence")
    payload: Payload
    signature: str = Field(description="base64url Ed25519 signature over the signing bytes")
    signer_public_key: str = Field(description="base64url Ed25519 public key of the device key")

    def signing_bytes(self) -> bytes:
        """Canonical bytes that are signed.

        JSON of {device_id, sequence, payload} with sorted keys — must match
        ``gateway`` verification exactly.
        """
        body = {
            "device_id": self.device_id,
            "sequence": self.sequence,
            "payload": json.loads(self.payload.model_dump_json()),
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SyncBatch(BaseModel):
    """Batch pushed by the sync engine to the gateway over mTLS."""

    device_id: str
    records: list[SignedRecord]


class RecordAck(BaseModel):
    """Per-record acknowledgement returned by the gateway."""

    device_id: str
    sequence: int
    status: Literal["accepted", "duplicate", "rejected"]
    detail: Optional[str] = None


class SyncBatchResult(BaseModel):
    device_id: str
    acks: list[RecordAck]


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def b64u_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii"))


class SyncedRecordValidator(BaseModel):
    """Validates an arbitrary payload dict coming back from the DB."""

    payload: Payload

    @field_validator("payload", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if isinstance(v, str):
            return json.loads(v)
        return v
