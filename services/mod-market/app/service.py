"""Domain service for mod-market."""
from __future__ import annotations

import base64
import uuid
from datetime import date
from typing import Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import (
    DisputeAction,
    DisputeEvent,
    EdgeSignedRecord,
    EdgeSyncBatch,
    IngestedAck,
    Market,
    Stall,
    StallageTicket,
    TicketStatus,
    Trader,
    utcnow,
)


class NotFoundError(KeyError):
    pass


class ConflictError(ValueError):
    pass


def _verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(
            base64.urlsafe_b64decode(public_key_b64.encode())
        )
        key.verify(base64.urlsafe_b64decode(signature_b64.encode()), message)
        return True
    except (InvalidSignature, ValueError):
        return False


class MarketService:
    def __init__(self) -> None:
        self._markets: Dict[str, Market] = {}
        self._stalls: Dict[str, Stall] = {}
        self._traders: Dict[str, Trader] = {}
        self._tickets: Dict[str, StallageTicket] = {}
        self._ticket_by_stall_date: Dict[Tuple[str, date], str] = {}
        self._edge_dedupe: Dict[Tuple[str, int], str] = {}  # (device, seq) -> ticket_id
        self._dispute_log: Dict[str, List[DisputeEvent]] = {}

    # -- registry ------------------------------------------------------------

    def register_market(self, market: Market) -> Market:
        if market.market_id in self._markets:
            raise ConflictError(f"market {market.market_id!r} exists")
        self._markets[market.market_id] = market
        return market

    def register_stall(self, stall: Stall) -> Stall:
        market = self._markets.get(stall.market_id)
        if market is None:
            raise NotFoundError(f"market {stall.market_id!r} not found")
        if stall.stall_id in self._stalls:
            raise ConflictError(f"stall {stall.stall_id!r} exists")
        active = [s for s in self._stalls.values() if s.market_id == stall.market_id and s.active]
        if len(active) >= market.stall_capacity:
            raise ConflictError(f"market {stall.market_id!r} at capacity")
        self._stalls[stall.stall_id] = stall
        return stall

    def enumerate_trader(self, trader: Trader) -> Trader:
        if trader.market_id not in self._markets:
            raise NotFoundError(f"market {trader.market_id!r} not found")
        if trader.stall_id and trader.stall_id not in self._stalls:
            raise NotFoundError(f"stall {trader.stall_id!r} not found")
        self._traders[trader.trader_id] = trader
        return trader

    def list_traders(self, market_id: Optional[str] = None) -> List[Trader]:
        traders = list(self._traders.values())
        return [t for t in traders if t.market_id == market_id] if market_id else traders

    # -- stallage e-ticketing --------------------------------------------------

    def issue_ticket(
        self,
        stall_id: str,
        service_date: date,
        trader_id: Optional[str] = None,
        *,
        origin: str = "ONLINE",
        edge_device_id: Optional[str] = None,
        edge_sequence: Optional[int] = None,
        edge_signature: Optional[str] = None,
    ) -> StallageTicket:
        stall = self._stalls.get(stall_id)
        if stall is None or not stall.active:
            raise NotFoundError(f"stall {stall_id!r} unknown/inactive")
        key = (stall_id, service_date)
        if key in self._ticket_by_stall_date:
            raise ConflictError(
                f"stallage already collected for stall {stall_id!r} on {service_date}"
            )
        market = self._markets[stall.market_id]
        ticket = StallageTicket(
            ticket_id=f"STL-{uuid.uuid4().hex[:12]}",
            state_id=market.state_id,
            market_id=stall.market_id,
            stall_id=stall_id,
            trader_id=trader_id,
            service_date=service_date,
            amount_kobo=stall.daily_fee_kobo,
            origin=origin,  # type: ignore[arg-type]
            edge_device_id=edge_device_id,
            edge_sequence=edge_sequence,
            edge_signature=edge_signature,
        )
        self._tickets[ticket.ticket_id] = ticket
        self._ticket_by_stall_date[key] = ticket.ticket_id
        return ticket

    def get_ticket(self, ticket_id: str) -> StallageTicket:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise NotFoundError(f"ticket {ticket_id!r} not found")
        return ticket

    # -- offline ticket ingestion (edge-daemon batch format) -------------------

    def ingest_edge_batch(self, batch: EdgeSyncBatch) -> List[IngestedAck]:
        """Consume a signed offline batch from a POS collector.

        Verifies every Ed25519 signature, enforces idempotent dedupe on
        ``(device_id, sequence)``, and maps revenue-ticket payloads with
        levy code 130 onto stallage tickets. Only records with valid
        signatures are accepted; per-record acks mirror the edge gateway.
        """
        acks: List[IngestedAck] = []
        for rec in batch.records:
            acks.append(self._ingest_record(rec))
        return acks

    def _ingest_record(self, rec: EdgeSignedRecord) -> IngestedAck:
        key = (rec.device_id, rec.sequence)
        if key in self._edge_dedupe:
            return IngestedAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="duplicate",
                ticket_id=self._edge_dedupe[key],
            )
        if not _verify(rec.signer_public_key, rec.signing_bytes(), rec.signature):
            return IngestedAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="rejected",
                detail="invalid signature",
            )
        if rec.payload.levy_code != "130":
            return IngestedAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="rejected",
                detail=f"unsupported levy code {rec.payload.levy_code!r} for mod-market",
            )
        stall_id = rec.payload.location or rec.payload.payer_id
        service_date = rec.payload.issued_at.date()
        try:
            ticket = self.issue_ticket(
                stall_id,
                service_date,
                trader_id=None,
                origin="OFFLINE_EDGE",
                edge_device_id=rec.device_id,
                edge_sequence=rec.sequence,
                edge_signature=rec.signature,
            )
        except NotFoundError:
            return IngestedAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="rejected",
                detail=f"stall {stall_id!r} unknown",
            )
        except ConflictError:
            # Same stall+day already ticketed — record the dedupe so retries
            # ack as duplicates rather than double-charging the trader.
            self._edge_dedupe[key] = self._ticket_by_stall_date[(stall_id, service_date)]
            return IngestedAck(
                device_id=rec.device_id,
                sequence=rec.sequence,
                status="duplicate",
                ticket_id=self._edge_dedupe[key],
                detail="stallage already collected for that stall/day",
            )
        self._edge_dedupe[key] = ticket.ticket_id
        return IngestedAck(
            device_id=rec.device_id,
            sequence=rec.sequence,
            status="accepted",
            ticket_id=ticket.ticket_id,
        )

    # -- dispute workflow (append-only arbitration-grade audit trail) -----------

    #: Resulting ticket status per action (None = status unchanged).
    _NEW_STATUS = {
        DisputeAction.OPENED: TicketStatus.UNDER_DISPUTE,
        DisputeAction.EVIDENCE_ATTACHED: None,
        DisputeAction.ESCALATED_TO_ARBITRATION: None,  # still UNDER_DISPUTE
        DisputeAction.RESOLVED_UPHELD: TicketStatus.RESOLVED_UPHELD,
        DisputeAction.RESOLVED_REFUNDED: TicketStatus.RESOLVED_REFUNDED,
    }

    def record_dispute_event(self, event: DisputeEvent) -> DisputeEvent:
        ticket = self.get_ticket(event.ticket_id)
        log = self._dispute_log.setdefault(event.ticket_id, [])

        if event.action == DisputeAction.OPENED:
            if ticket.status != TicketStatus.PAID:
                raise ConflictError("ticket is already under dispute or resolved")
        elif event.action == DisputeAction.EVIDENCE_ATTACHED:
            if not log:
                raise ConflictError("cannot attach evidence before opening a dispute")
        elif event.action == DisputeAction.ESCALATED_TO_ARBITRATION:
            if ticket.status != TicketStatus.UNDER_DISPUTE:
                raise ConflictError("only open disputes can be escalated")
        else:  # resolutions
            if ticket.status != TicketStatus.UNDER_DISPUTE:
                raise ConflictError("dispute is not open")

        # The log is append-only by construction: no update/delete paths exist.
        log.append(event)
        new_status = self._NEW_STATUS[event.action]
        if new_status is not None:
            ticket.status = new_status
        return event

    def dispute_trail(self, ticket_id: str) -> List[DisputeEvent]:
        self.get_ticket(ticket_id)
        return list(self._dispute_log.get(ticket_id, []))
