"""Domain: fare tables, tap/ticket clearing, operator settlement, Cowry bridge.

TigerBeetle split semantics (ledger/chart-of-accounts.md):
- transfer code 140 — transit ticketing
- account 1001 Payer Clearing (fare pool), 3001 State CRF (TSA),
  4002 Transport Union Commission Pool (gazetted 3–8% auto-split),
  2010 MDA/operator retention.
Settlement splits execute as linked atomic transfer chains in production.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel, Field

# --- _shared.hashchain import guard (idiom mirrors mod-waterways) ------------
try:
    from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash, verify_event_chain
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.hashchain import (
            GENESIS_PREV_HASH,
            event_payload_hash,
            verify_event_chain,
        )
    except ImportError:  # minimal container: ship a local fallback copy
        GENESIS_PREV_HASH = "0" * 64

        def _canonical(obj):
            return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

        def event_payload_hash(payload, prev_hash):
            body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
            body["prev_hash"] = prev_hash
            return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

        def verify_event_chain(events):
            errors, last_hash = [], None
            for index, event in enumerate(events):
                label = event.get("event_id", f"index-{index}")
                prev = event.get("prev_hash")
                expected = GENESIS_PREV_HASH if last_hash is None else last_hash
                if prev != expected:
                    errors.append(f"event {label}: broken chain link")
                recorded = event.get("event_hash")
                if recorded != event_payload_hash(event, prev if prev is not None else ""):
                    errors.append(f"event {label}: hash mismatch — record tampered")
                last_hash = recorded
            return errors


def request_hash(payload: object) -> str:
    """Canonical SHA-256 request hash (mirrors ledger/fundsflow/idempotency.py)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

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


class EscrowState(str, Enum):
    PENDING = "pending"
    POSTED = "posted"
    VOID = "void"


class BillEventConflict(RuntimeError):
    """Same bill_reference redelivered with a different payload (HTTP 409)."""

    status_code = 409


class SettlementRejectedError(ValueError):
    """Settlement batch failed validation (e.g. zero legs) — HTTP 409."""


class EscrowExpiredError(ValueError):
    """Pending escrow past its expiry — auto-aborted, HTTP 409."""


class SchemeCallbackConflict(RuntimeError):
    """Same transfer_id callback redelivered with a different payload (409)."""

    status_code = 409


#: Default pending-escrow TTL (Mojaloop-style transfer expiration window).
DEFAULT_ESCROW_TTL_SECONDS = 15 * 60

#: Total basis points for integer split math (mirrors ledger/splits/split.go).
TOTAL_BASIS_POINTS = 10_000

#: Reference state CRF share in basis points (10%).
CRF_SHARE_BPS = 1_000


class EscrowRecord(BaseModel):
    """Pending-transfer escrow for a settlement leg batch (Mojaloop seam).

    Lifecycle: PENDING on prepare (funds reserved on the escrow account),
    POSTED on fulfil (FSPIOP COMMITTED), VOID on abort. Idempotent on
    transfer_id — replays return the original record unchanged.
    """

    transfer_id: str
    batch_id: str
    amount_kobo: int
    condition: str
    state: EscrowState
    fulfilment: str | None = None
    created_at: str
    expires_at: str  # PENDING escrows past this instant are auto-aborted
    completed_at: str | None = None


class SettlementBatch(BaseModel):
    batch_id: str
    tenant_state_id: str
    operator_id: str
    record_count: int
    gross_kobo: int
    legs: list[SettlementLeg]
    created_at: str


class MobilityStore:
    """Thread-safe in-memory store. Production: Redis + TigerBeetle + Mojaloop.

    ``clock`` is injectable (epoch seconds) for deterministic escrow-expiry
    tests. ``ledger`` is the settlement-leg execution adapter
    (hold → linked post chain); defaults to the deterministic fixture.
    """

    def __init__(self, ledger=None, clock: Callable[[], float] = time.time,
                 escrow_ttl_seconds: int = DEFAULT_ESCROW_TTL_SECONDS) -> None:
        if ledger is None:
            from .adapters.ledger import select_ledger_adapter

            ledger = select_ledger_adapter()
        self.ledger = ledger
        self._clock = clock
        self.escrow_ttl_seconds = escrow_ttl_seconds
        self._lock = threading.Lock()
        self.fare_tables: dict[str, FareTable] = {}
        self.clearing: dict[str, ClearingRecord] = {}
        self.batches: dict[str, SettlementBatch] = {}
        self.escrows: dict[str, EscrowRecord] = {}  # keyed by transfer_id
        self.bill_events: dict[str, dict] = {}  # keyed by bill_reference
        # transfer_id -> request_hash of the accepted FSPIOP fulfilment callback
        self.scheme_callback_hashes: dict[str, str] = {}
        self.audit_chain: list[dict] = []  # hash-chained audit records
        self._chain_tip: str = GENESIS_PREV_HASH

    # --- hash-chained audit ----------------------------------------------------
    def _audit(self, event_type: str, payload: dict) -> dict:
        """Append a hash-chained audit record (P1 immutability)."""
        record = dict(payload)
        record["event_id"] = _id("aud")
        record["event_type"] = event_type
        record["recorded_at"] = _now()
        record["prev_hash"] = self._chain_tip
        record["event_hash"] = event_payload_hash(record, self._chain_tip)
        self._chain_tip = record["event_hash"]
        self.audit_chain.append(record)
        return record

    def verify_audit_chain(self) -> list[str]:
        with self._lock:
            return verify_event_chain(list(self.audit_chain))

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
    @staticmethod
    def _muldiv_floor(amount: int, bps: int) -> int:
        """floor(amount * bps / 10_000) in pure integer arithmetic."""
        return amount * bps // TOTAL_BASIS_POINTS

    def settle_operator(self, tenant_state_id: str, operator_id: str) -> SettlementBatch:
        """Close all unsettled clearing records for an operator into a batch with
        TigerBeetle split legs (union commission, CRF share, operator retention).

        Integer basis-point mul-div with last-leg remainder (mirrors
        ledger/splits/split.go): legs always sum exactly to gross, never
        float. Batches containing a zero leg are rejected BEFORE any record
        is marked settled. Legs execute as a linked hold → post chain on the
        settlement ledger adapter (fail-closed seam)."""
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
            union_bps = int(round(table.union_commission_pct * 100))
            union = self._muldiv_floor(gross, union_bps)
            crf = self._muldiv_floor(gross, CRF_SHARE_BPS)
            operator = gross - union - crf  # last leg absorbs the remainder
            legs = [
                SettlementLeg(beneficiary="TRANSPORT_UNION_COMMISSION",
                              tigerbeetle_account_code=ACCOUNT_UNION_COMMISSION,
                              amount_kobo=union),
                SettlementLeg(beneficiary="STATE_CONSOLIDATED_REVENUE_FUND",
                              tigerbeetle_account_code=ACCOUNT_STATE_CRF,
                              amount_kobo=crf),
                SettlementLeg(beneficiary="OPERATOR_RETENTION",
                              tigerbeetle_account_code=2010,
                              amount_kobo=operator),
            ]
            zero = [l.beneficiary for l in legs if l.amount_kobo <= 0]
            if zero:
                raise SettlementRejectedError(
                    f"settlement batch for operator '{operator_id}' has zero-value "
                    f"legs ({', '.join(zero)}); refusing to mark records settled")
            batch = SettlementBatch(
                batch_id=_id("stl"), tenant_state_id=tenant_state_id,
                operator_id=operator_id, record_count=len(pending), gross_kobo=gross,
                legs=legs, created_at=_now(),
            )
            # Execute legs as a linked hold → post chain BEFORE closing the
            # batch; a ledger failure leaves every record unsettled.
            postings = self.ledger.execute_linked_chain(batch.batch_id, legs)
            self.batches[batch.batch_id] = batch
            for r in pending:
                r.settled_batch_id = batch.batch_id
            self._audit("settlement_executed", {
                "batch_id": batch.batch_id,
                "tenant_state_id": tenant_state_id,
                "operator_id": operator_id,
                "gross_kobo": gross,
                "legs": [l.model_dump() for l in legs],
                "ledger_postings": postings,
            })
            return batch

    # --- pending-transfer escrow (Mojaloop FSPIOP seam) -----------------------
    def _iso(self, epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")

    def begin_escrow(self, transfer_id: str, batch_id: str, amount_kobo: int,
                     condition: str) -> EscrowRecord:
        """Reserve funds on the escrow account (FSPIOP prepare → PENDING).

        Idempotent on transfer_id: a replay returns the original record.
        Each new escrow carries an ``expires_at`` (default 15 min from the
        injected clock); expired PENDING escrows are auto-aborted by
        :meth:`sweep_expired_escrows` and rejected on fulfil.
        """
        with self._lock:
            existing = self.escrows.get(transfer_id)
            if existing is not None:
                if existing.batch_id != batch_id or existing.amount_kobo != amount_kobo:
                    raise ValueError(
                        f"transfer_id '{transfer_id}' already escrowed with different terms")
                return existing
            if batch_id not in self.batches:
                raise KeyError(f"settlement batch '{batch_id}' not found")
            now = self._clock()
            rec = EscrowRecord(transfer_id=transfer_id, batch_id=batch_id,
                               amount_kobo=amount_kobo, condition=condition,
                               state=EscrowState.PENDING, created_at=self._iso(now),
                               expires_at=self._iso(now + self.escrow_ttl_seconds))
            self.escrows[transfer_id] = rec
            return rec

    def _abort_locked(self, rec: EscrowRecord, reason: str) -> EscrowRecord:
        rec.state = EscrowState.VOID
        rec.completed_at = self._iso(self._clock())
        self._audit("escrow_aborted", {
            "transfer_id": rec.transfer_id, "batch_id": rec.batch_id,
            "amount_kobo": rec.amount_kobo, "reason": reason,
        })
        return rec

    def _expired(self, rec: EscrowRecord) -> bool:
        expires = datetime.fromisoformat(rec.expires_at).timestamp()
        return expires <= self._clock()

    def fulfil_escrow(self, transfer_id: str, fulfilment: str) -> EscrowRecord:
        """Post a pending escrow on FSPIOP COMMITTED fulfilment (idempotent).

        An expired PENDING escrow is auto-aborted and rejected (409)."""
        with self._lock:
            rec = self.escrows.get(transfer_id)
            if rec is None:
                raise KeyError(f"escrow '{transfer_id}' not found")
            if rec.state == EscrowState.POSTED:
                return rec  # idempotent replay
            if rec.state != EscrowState.PENDING:
                raise ValueError(f"escrow '{transfer_id}' is {rec.state.value}")
            if self._expired(rec):
                self._abort_locked(rec, "expired before fulfilment")
                raise EscrowExpiredError(
                    f"escrow '{transfer_id}' expired at {rec.expires_at}; aborted")
            rec.state = EscrowState.POSTED
            rec.fulfilment = fulfilment
            rec.completed_at = self._iso(self._clock())
            return rec

    def abort_escrow(self, transfer_id: str) -> EscrowRecord:
        """Void a pending escrow on FSPIOP ABORTED / expiry (idempotent)."""
        with self._lock:
            rec = self.escrows.get(transfer_id)
            if rec is None:
                raise KeyError(f"escrow '{transfer_id}' not found")
            if rec.state == EscrowState.VOID:
                return rec  # idempotent replay
            if rec.state != EscrowState.PENDING:
                raise ValueError(f"escrow '{transfer_id}' is {rec.state.value}")
            return self._abort_locked(rec, "aborted")

    def apply_transfer_callback(self, payload: dict) -> EscrowRecord:
        """Apply an authenticated FSPIOP PUT /transfers/{id} callback.

        COMMITTED posts the pending escrow; ABORTED voids it. Idempotent on
        transfer_id: the canonical ``request_hash`` of the callback payload is
        stored; a redelivery with the same payload replays the current record,
        while the same transfer_id with a DIFFERENT payload raises
        :class:`SchemeCallbackConflict` (HTTP 409) and is audit-logged on the
        hash chain. Signature verification happens at the HTTP edge; this
        method only sees authenticated payloads.
        """
        transfer_id = payload.get("transferId")
        state = str(payload.get("transferState") or "").upper()
        if not transfer_id:
            raise KeyError("callback payload missing 'transferId'")
        if state not in ("COMMITTED", "ABORTED"):
            raise ValueError(f"unsupported transferState '{state}'")
        digest = request_hash(payload)
        with self._lock:
            seen = self.scheme_callback_hashes.get(transfer_id)
            if seen is not None:
                if seen != digest:
                    self._audit("scheme_callback_conflict", {
                        "transfer_id": transfer_id,
                        "existing_request_hash": seen,
                        "rejected_request_hash": digest,
                    })
                    raise SchemeCallbackConflict(
                        f"transfer_id '{transfer_id}' callback redelivered with a "
                        f"different payload")
                rec = self.escrows.get(transfer_id)
                if rec is None:
                    raise KeyError(f"escrow '{transfer_id}' not found")
                return rec  # idempotent replay of the accepted callback
        if state == "COMMITTED":
            rec = self.fulfil_escrow(transfer_id,
                                     str(payload.get("fulfilment") or ""))
        else:
            rec = self.abort_escrow(transfer_id)
        with self._lock:
            self.scheme_callback_hashes[transfer_id] = digest
            self._audit("scheme_callback_applied", {
                "transfer_id": transfer_id,
                "transfer_state": state,
                "request_hash": digest,
                "escrow_state": rec.state.value,
                "fulfilment": rec.fulfilment,
            })
        return rec

    def sweep_expired_escrows(self) -> list[EscrowRecord]:
        """Auto-abort every expired PENDING escrow (FSPIOP expiry sweep).

        Returns the records voided by this sweep; idempotent — already VOID
        escrows are not re-audited."""
        with self._lock:
            voided = []
            for rec in self.escrows.values():
                if rec.state == EscrowState.PENDING and self._expired(rec):
                    voided.append(self._abort_locked(rec, "expired (sweep)"))
            return voided

    def record_bill_event(self, bill_reference: str, event: dict) -> dict:
        """Idempotently record a NIBSS e-Bills payment notification.

        Stores the canonical ``request_hash`` of the notification payload
        (excluding receipt metadata) with the event. A redelivery with the
        same reference AND same payload replays the original; the same
        reference with a DIFFERENT payload raises :class:`BillEventConflict`
        (HTTP 409) and is audit-logged on the hash chain."""
        payload = {k: v for k, v in event.items() if k != "received_at"}
        digest = request_hash(payload)
        with self._lock:
            existing = self.bill_events.get(bill_reference)
            if existing is not None:
                if existing["request_hash"] != digest:
                    conflict = {
                        "bill_reference": bill_reference,
                        "existing_request_hash": existing["request_hash"],
                        "rejected_request_hash": digest,
                        "existing_amount_kobo": existing["event"].get("amount_kobo"),
                        "rejected_amount_kobo": event.get("amount_kobo"),
                    }
                    self._audit("bill_event_conflict", conflict)
                    raise BillEventConflict(
                        f"bill_reference '{bill_reference}' redelivered with a "
                        f"different payload (amount "
                        f"{conflict['existing_amount_kobo']} → "
                        f"{conflict['rejected_amount_kobo']} kobo)")
                return existing["event"]
            stored = {"request_hash": digest, "event": event}
            self.bill_events[bill_reference] = stored
            self._audit("bill_event_recorded", {
                "bill_reference": bill_reference, "request_hash": digest,
                "amount_kobo": event.get("amount_kobo"),
            })
            return event


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
