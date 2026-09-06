"""Domain: fare tables, tap/ticket clearing, operator settlement, Cowry bridge.

TigerBeetle split semantics (ledger/chart-of-accounts.md):
- transfer code 140 — transit ticketing
- account 1001 Payer Clearing (fare pool), 3001 State CRF (TSA),
  4002 Transport Union Commission Pool (gazetted 3–8% auto-split),
  2010 MDA/operator retention.
Settlement splits execute as linked atomic transfer chains in production.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

TRANSFER_CODE_TRANSIT = 140
ACCOUNT_FARE_CLEARING = 1001
ACCOUNT_STATE_CRF = 3001
ACCOUNT_UNION_COMMISSION = 4002

#: Gazetted union commission band (mod-mobility-switch README: 3–8%).
UNION_COMMISSION_MIN_PCT = 3.0
UNION_COMMISSION_MAX_PCT = 8.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class Mode(str, Enum):
    BUS = "bus"
    RAIL = "rail"
    FERRY = "ferry"


class FareRule(BaseModel):
    mode: Mode
    route: str  # e.g. "BRT-IKORODU-CMS" or "*" wildcard
    fare_kobo: int = Field(..., ge=0)


class FareTable(BaseModel):
    tenant_state_id: str
    gazette_reference: str  # LAMATA ticketing harmonization gazette dependency
    union_commission_pct: float = Field(
        ..., ge=UNION_COMMISSION_MIN_PCT, le=UNION_COMMISSION_MAX_PCT,
        description="Gazetted union commission auto-split (3–8%)")
    fares: list[FareRule]
    updated_at: str


class ClearingRecord(BaseModel):
    """A settled tap/ticket event awaiting operator settlement."""

    record_id: str
    tenant_state_id: str
    operator_id: str
    mode: Mode
    route: str
    fare_kobo: int
    card_ref: str  # Cowry-compatible card token (opaque, non-PII)
    tapped_at: str
    settled_batch_id: str | None = None


class SettlementLeg(BaseModel):
    beneficiary: str
    tigerbeetle_account_code: int
    amount_kobo: int
    transfer_code: int = TRANSFER_CODE_TRANSIT


class SettlementBatch(BaseModel):
    batch_id: str
    tenant_state_id: str
    operator_id: str
    record_count: int
    gross_kobo: int
    legs: list[SettlementLeg]
    created_at: str


class MobilityStore:
    """Thread-safe in-memory store. Production: Redis + TigerBeetle + Mojaloop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fare_tables: dict[str, FareTable] = {}
        self.clearing: dict[str, ClearingRecord] = {}
        self.batches: dict[str, SettlementBatch] = {}

    # --- fare table config ----------------------------------------------------
    def set_fare_table(self, table: FareTable) -> FareTable:
        with self._lock:
            self.fare_tables[table.tenant_state_id] = table
        return table

    def lookup_fare(self, tenant_state_id: str, mode: Mode, route: str) -> int:
        table = self.fare_tables.get(tenant_state_id)
        if table is None:
            raise KeyError(f"no fare table configured for '{tenant_state_id}'")
        for rule in table.fares:
            if rule.mode == mode and rule.route == route:
                return rule.fare_kobo
        for rule in table.fares:  # wildcard fallback
            if rule.mode == mode and rule.route == "*":
                return rule.fare_kobo
        raise KeyError(f"no fare for {mode.value}:{route} in '{tenant_state_id}'")

    # --- tap/ticket clearing ----------------------------------------------------
    def record_tap(self, tenant_state_id: str, operator_id: str, mode: Mode,
                   route: str, card_ref: str) -> ClearingRecord:
        fare = self.lookup_fare(tenant_state_id, mode, route)
        rec = ClearingRecord(record_id=_id("tap"), tenant_state_id=tenant_state_id,
                             operator_id=operator_id, mode=mode, route=route,
                             fare_kobo=fare, card_ref=card_ref, tapped_at=_now())
        with self._lock:
            self.clearing[rec.record_id] = rec
        return rec

    # --- operator settlement -----------------------------------------------------
    def settle_operator(self, tenant_state_id: str, operator_id: str) -> SettlementBatch:
        """Close all unsettled clearing records for an operator into a batch with
        TigerBeetle split legs (union commission, CRF share, operator retention)."""
        with self._lock:
            table = self.fare_tables.get(tenant_state_id)
            if table is None:
                raise KeyError(f"no fare table configured for '{tenant_state_id}'")
            pending = [r for r in self.clearing.values()
                       if r.tenant_state_id == tenant_state_id
                       and r.operator_id == operator_id
                       and r.settled_batch_id is None]
            if not pending:
                raise ValueError(f"no unsettled clearing records for operator '{operator_id}'")
            gross = sum(r.fare_kobo for r in pending)
            union = round(gross * table.union_commission_pct / 100)
            crf = round(gross * 0.10)  # 10% state CRF share (reference rate)
            operator = gross - union - crf
            batch = SettlementBatch(
                batch_id=_id("stl"), tenant_state_id=tenant_state_id,
                operator_id=operator_id, record_count=len(pending), gross_kobo=gross,
                legs=[
                    SettlementLeg(beneficiary="TRANSPORT_UNION_COMMISSION",
                                  tigerbeetle_account_code=ACCOUNT_UNION_COMMISSION,
                                  amount_kobo=union),
                    SettlementLeg(beneficiary="STATE_CONSOLIDATED_REVENUE_FUND",
                                  tigerbeetle_account_code=ACCOUNT_STATE_CRF,
                                  amount_kobo=crf),
                    SettlementLeg(beneficiary="OPERATOR_RETENTION",
                                  tigerbeetle_account_code=2010,
                                  amount_kobo=operator),
                ],
                created_at=_now(),
            )
            self.batches[batch.batch_id] = batch
            for r in pending:
                r.settled_batch_id = batch.batch_id
            return batch


def cowry_authorize(card_ref: str, fare_kobo: int) -> dict:
    """Cowry-compatible card bridge interface stub.

    Production: EMV/QR authorization against the Cowry Gen 2 host (offline-
    capable, store-and-forward). The stub authorizes deterministically: card
    tokens ending in an even hex digit are approved, odd are declined.
    """
    approved = bool(card_ref) and int(card_ref[-1], 16) % 2 == 0
    return {
        "bridge": "cowry-gen2-stub",
        "card_ref": card_ref,
        "fare_kobo": fare_kobo,
        "decision": "APPROVED" if approved else "DECLINED",
        "offline_capable": True,
    }
