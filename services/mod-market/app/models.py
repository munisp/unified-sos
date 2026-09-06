"""Domain models for mod-market — markets, traders, stallage, disputes."""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketType(str, enum.Enum):
    DAILY_MARKET = "DAILY_MARKET"
    WEEKLY_MARKET = "WEEKLY_MARKET"
    MOTOR_PARK = "MOTOR_PARK"
    NIGHT_MARKET = "NIGHT_MARKET"


class Market(BaseModel):
    market_id: str
    state_id: str
    name: str
    market_type: MarketType
    lga: str
    stall_capacity: int = Field(ge=1)
    active: bool = True
    registered_at: datetime = Field(default_factory=utcnow)


class Stall(BaseModel):
    stall_id: str
    market_id: str
    block: str
    number: str
    daily_fee_kobo: int = Field(ge=0, description="Policy-pack configured stallage rate")
    active: bool = True


class Trader(BaseModel):
    trader_id: str
    state_id: str
    full_name: str
    phone: str
    market_id: str
    stall_id: Optional[str] = None
    stin: Optional[str] = Field(default=None, description="State TIN if issued")
    enumerated_at: datetime = Field(default_factory=utcnow)

    @property
    def masked_phone(self) -> str:
        """Masked MSISDN — country prefix + last two digits only."""
        tail = self.phone[-2:]
        return f"{self.phone[:4]}****{tail}" if len(self.phone) > 6 else f"****{tail}"


class TraderRead(BaseModel):
    """Read model for trader records: masked phone only (NDPA gate).

    Raw MSISDNs structurally cannot leave the API — every trader-serving
    endpoint uses this response model.
    """

    trader_id: str
    state_id: str
    full_name: str
    masked_phone: str
    market_id: str
    stall_id: Optional[str] = None
    stin: Optional[str] = None
    enumerated_at: datetime


class TicketStatus(str, enum.Enum):
    PAID = "PAID"
    UNDER_DISPUTE = "UNDER_DISPUTE"
    RESOLVED_UPHELD = "RESOLVED_UPHELD"
    RESOLVED_REFUNDED = "RESOLVED_REFUNDED"


class StallageTicket(BaseModel):
    """Daily stallage e-ticket. ``(stall_id, service_date)`` is unique."""

    ticket_id: str
    state_id: str
    market_id: str
    stall_id: str
    trader_id: Optional[str] = None
    service_date: date
    amount_kobo: int = Field(ge=0)
    transfer_code: int = 130  # market stallage / micro-levy (chart-of-accounts)
    status: TicketStatus = TicketStatus.PAID
    origin: Literal["ONLINE", "OFFLINE_EDGE"] = "ONLINE"
    edge_device_id: Optional[str] = None
    edge_sequence: Optional[int] = None
    edge_signature: Optional[str] = None
    issued_at: datetime = Field(default_factory=utcnow)


class DisputeAction(str, enum.Enum):
    OPENED = "OPENED"
    EVIDENCE_ATTACHED = "EVIDENCE_ATTACHED"
    ESCALATED_TO_ARBITRATION = "ESCALATED_TO_ARBITRATION"
    RESOLVED_UPHELD = "RESOLVED_UPHELD"
    RESOLVED_REFUNDED = "RESOLVED_REFUNDED"


class DisputeEvent(BaseModel):
    """One entry in a ticket's append-only arbitration-grade audit trail."""

    event_id: str
    ticket_id: str
    action: DisputeAction
    actor_id: str = Field(description="trader / market master / arbitrator ID")
    note: Optional[str] = None
    evidence_ref: Optional[str] = None
    recorded_at: datetime = Field(default_factory=utcnow)


# ---- Edge-daemon offline batch ingestion contract ------------------------
# Mirrors edge/edge-daemon/edge_daemon/models.py (SignedRecord / SyncBatch).
# Kept as a local copy so the service does not depend on the edge package;
# the shapes must stay wire-compatible.


class EdgeTicketPayload(BaseModel):
    kind: Literal["revenue_ticket"] = "revenue_ticket"
    state_id: str
    bill_reference: str
    payer_id: str
    levy_code: str
    amount_kobo: int = Field(ge=0)
    collector_id: str
    location: Optional[str] = None
    issued_at: datetime


class EdgeSignedRecord(BaseModel):
    device_id: str
    sequence: int = Field(ge=1)
    payload: EdgeTicketPayload
    signature: str
    signer_public_key: str

    def signing_bytes(self) -> bytes:
        import json

        body = {
            "device_id": self.device_id,
            "sequence": self.sequence,
            "payload": json.loads(self.payload.model_dump_json()),
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EdgeSyncBatch(BaseModel):
    device_id: str
    records: List[EdgeSignedRecord]


class IngestedAck(BaseModel):
    device_id: str
    sequence: int
    status: Literal["accepted", "duplicate", "rejected"]
    ticket_id: Optional[str] = None
    detail: Optional[str] = None
