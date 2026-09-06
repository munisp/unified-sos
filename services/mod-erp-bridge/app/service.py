"""mod-erp-bridge service layer.

Ingests balanced journal entries (via API or settlement events on the shared
event bus), dedupes by ``source_event_id``, applies the per-state COA
mapping, pushes to the configured ERP backend, and records every push in a
hash-chained outbound log (services/_shared/hashchain.py). Push failures go
through a bounded retry queue with exponential backoff and land in a
dead-letter queue after ``max_attempts``.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

_SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))

try:
    from _shared.hashchain import (
        GENESIS_PREV_HASH,
        event_payload_hash,
        verify_event_chain,
    )
except ImportError:  # minimal container images ship only the app package
    import hashlib as _hashlib
    import json as _json

    GENESIS_PREV_HASH = "0" * 64

    def _canonical(obj):  # noqa: ANN001, ANN202
        return _json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

    def event_payload_hash(payload, prev_hash):  # noqa: ANN001, ANN201
        body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
        body["prev_hash"] = prev_hash
        return _hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

    def verify_event_chain(events):  # noqa: ANN001, ANN202
        errors = []
        last = None
        for index, event in enumerate(events):
            label = event.get("event_id", f"index-{index}")
            prev = event.get("prev_hash")
            expected = GENESIS_PREV_HASH if last is None else last
            if prev != expected:
                errors.append(f"event {label}: broken chain link")
            if event.get("event_hash") != event_payload_hash(event, prev or ""):
                errors.append(f"event {label}: hash mismatch — record tampered")
            last = event.get("event_hash")
        return errors

from .adapters import (  # noqa: E402
    AdapterUnavailableError,
    ErpAdapter,
    ErpPushError,
    FixtureErpAdapter,
    build_adapter,
)
from .domain import (  # noqa: E402
    CoaMapping,
    JournalEntry,
    JournalLine,
    OutboundRecord,
    PushStatus,
    TENANT_STATES,
    utcnow,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_CONFIG_DIR = _REPO_ROOT / "config" / "states"

# Settlement channel produced by the payments flow (WP-04); the bridge can
# subscribe to turn settlements into journal entries automatically.
SETTLEMENT_CHANNEL = "ng.sos.payments.settlement_completed"

# In-repo default state COA code -> ERP account map (used when the state pack
# ships no erp-coa.yaml override). Keys are platform COA codes.
DEFAULT_COA_MAP: Dict[str, str] = {
    "1000_CASH_TREASURY": "1000 Cash — Treasury Single Account",
    "1100_RECEIVABLE_MDA": "1100 Receivables — MDA",
    "2000_PAYABLE_CONCESSIONAIRE": "2000 Payables — PPP Concessionaire",
    "3000_CRF": "3000 Consolidated Revenue Fund",
    "4000_REVENUE_IGR": "4000 Revenue — Internally Generated Revenue",
    "5000_EXPENSE_OPERATIONS": "5000 Expenses — Operations",
}

# Settlement beneficiary -> (debit COA code, credit COA code) for the
# event-driven journal construction.
SETTLEMENT_ACCOUNT_MAP: Dict[str, tuple[str, str]] = {
    "STATE_CONSOLIDATED_REVENUE_FUND": ("1000_CASH_TREASURY", "3000_CRF"),
    "MDA_RETENTION": ("1000_CASH_TREASURY", "4000_REVENUE_IGR"),
    "PPP_CONCESSIONAIRE_ESCROW": ("1000_CASH_TREASURY", "2000_PAYABLE_CONCESSIONAIRE"),
}


class NotFoundError(RuntimeError):
    pass


class UnknownTenantError(NotFoundError):
    pass


class UnavailableError(RuntimeError):
    pass


class RetryItem(BaseModel):
    entry: JournalEntry
    attempts: int = 0
    next_attempt_at: datetime
    last_error: str = ""


def require_state(state: str) -> str:
    if state not in TENANT_STATES:
        raise UnknownTenantError(f"unknown tenant state {state!r}")
    return state


def _load_state_coa(state: str) -> Dict[str, str]:
    """Load a per-state COA override (config/states/<state>/erp-coa.yaml)."""
    path = _STATE_CONFIG_DIR / state / "erp-coa.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
        mapping = data.get("coa_mapping") or data.get("mapping") or {}
        if isinstance(mapping, dict):
            return {str(k): str(v) for k, v in mapping.items()}
    except Exception:  # noqa: BLE001 — unreadable override falls back to default
        return {}
    return {}


class ErpBridgeService:
    def __init__(
        self,
        adapter: Optional[ErpAdapter] = None,
        bus: Optional[object] = None,
        max_attempts: int = 3,
        backoff_base_s: float = 60.0,
    ) -> None:
        self.adapter = adapter or FixtureErpAdapter()
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self._entries: Dict[str, Dict[str, JournalEntry]] = {s: {} for s in TENANT_STATES}
        # Dedupe index: state -> source_event_id -> entry_id
        self._by_source: Dict[str, Dict[str, str]] = {s: {} for s in TENANT_STATES}
        self._coa: Dict[str, CoaMapping] = {
            s: CoaMapping(
                tenant_state_id=s, mapping={**DEFAULT_COA_MAP, **_load_state_coa(s)}
            )
            for s in TENANT_STATES
        }
        self._outbound: List[OutboundRecord] = []
        self._retry: List[RetryItem] = []
        self._dead_letter: List[JournalEntry] = []
        self._bus: Optional[object] = bus
        if bus is not None:
            self.subscribe_settlements(bus)

    # ---------------- COA mapping ----------------

    def get_coa_mapping(self, state: str) -> CoaMapping:
        return self._coa[require_state(state)]

    def set_coa_mapping(self, state: str, mapping: Dict[str, str]) -> CoaMapping:
        coa = CoaMapping(tenant_state_id=require_state(state), mapping=dict(mapping))
        self._coa[state] = coa
        return coa

    # ---------------- ingestion ----------------

    def ingest(self, entry: JournalEntry) -> OutboundRecord:
        """Push one journal entry; replayed ``source_event_id`` is deduped."""
        state = require_state(entry.tenant_state_id)
        existing = self._by_source[state].get(entry.source_event_id)
        if existing is not None:
            prior = self._entries[state][existing]
            record = self._record(prior, status=PushStatus.DEDUPED, receipt=None)
            return record
        try:
            receipt = self.adapter.push_journal(entry, self._coa[state].mapping)
        except ErpPushError as exc:
            self._entries[state][entry.entry_id] = entry
            self._by_source[state][entry.source_event_id] = entry.entry_id
            self._enqueue_retry(entry, str(exc))
            return self._record(entry, status=PushStatus.RETRY_PENDING, receipt=None)
        self._entries[state][entry.entry_id] = entry
        self._by_source[state][entry.source_event_id] = entry.entry_id
        return self._record(entry, status=PushStatus.PUSHED, receipt=receipt)

    def ingest_settlement(self, payload: BaseModel) -> Optional[OutboundRecord]:
        """Convert a ``ng.sos.payments.settlement_completed`` payload into a
        balanced journal entry and ingest it (deduped on replay)."""
        data = payload.model_dump() if isinstance(payload, BaseModel) else dict(payload)
        state = data.get("state_id")
        if state not in TENANT_STATES:
            return None  # not our tenant; ignore quietly
        lines: List[JournalLine] = []
        for split in data.get("splits", []):
            accounts = SETTLEMENT_ACCOUNT_MAP.get(
                split.get("beneficiary"), ("1000_CASH_TREASURY", "4000_REVENUE_IGR")
            )
            amount = int(split.get("amount_kobo", 0))
            if amount <= 0:
                continue
            lines.append(JournalLine(account_code=accounts[0], debit_kobo=amount))
            lines.append(JournalLine(account_code=accounts[1], credit_kobo=amount))
        if not lines:
            return None
        ts = str(data.get("timestamp", ""))[:10]
        entry = JournalEntry(
            entry_id=f"SETTLE-{data.get('bill_reference', 'unknown')}",
            tenant_state_id=state,
            date=date.fromisoformat(ts) if ts else utcnow().date(),
            memo=f"Settlement {data.get('bill_reference', '')}",
            lines=lines,
            source_event_id=f"settlement:{data.get('bill_reference', '')}",
        )
        return self.ingest(entry)

    def subscribe_settlements(self, bus: object) -> None:
        """Optional subscription to settlement events via the shared eventbus.

        The topic registry may not be loadable in minimal containers; failure
        to resolve the topic degrades to API-only ingestion (never crashes).
        """
        topic = SETTLEMENT_CHANNEL
        try:
            from _shared.eventbus import topic_for_channel

            topic = topic_for_channel(SETTLEMENT_CHANNEL)
        except Exception:  # noqa: BLE001 — registry absent; use channel as topic
            pass
        bus.subscribe(topic, self.ingest_settlement)

    # ---------------- queries ----------------

    def list_journals(self, state: str) -> List[JournalEntry]:
        return list(self._entries[require_state(state)].values())

    def get_journal(self, state: str, entry_id: str) -> JournalEntry:
        try:
            return self._entries[require_state(state)][entry_id]
        except KeyError:
            raise NotFoundError(f"journal {entry_id!r} not found in state {state!r}")

    def receipt_for(self, state: str, entry_id: str) -> Optional[object]:
        self.get_journal(state, entry_id)
        for record in reversed(self._outbound):
            if (
                record.tenant_state_id == state
                and record.entry_id == entry_id
                and record.status == PushStatus.PUSHED
            ):
                return record.receipt
        return None

    def dead_letters(self, state: Optional[str] = None) -> List[JournalEntry]:
        if state is None:
            return list(self._dead_letter)
        require_state(state)
        return [e for e in self._dead_letter if e.tenant_state_id == state]

    def retry_depth(self) -> int:
        return len(self._retry)

    # ---------------- outbound hash chain ----------------

    def _record(
        self,
        entry: JournalEntry,
        status: PushStatus,
        receipt: Optional[object],
    ) -> OutboundRecord:
        prev = self._outbound[-1].event_hash if self._outbound else GENESIS_PREV_HASH
        body = {
            "event_id": f"out-{len(self._outbound) + 1:06d}",
            "tenant_state_id": entry.tenant_state_id,
            "entry_id": entry.entry_id,
            "source_event_id": entry.source_event_id,
            "entry_hash": entry.hash,
            "status": status.value,
            "receipt": receipt.model_dump(mode="json") if receipt else None,
            "timestamp": utcnow().isoformat(),
        }
        record = OutboundRecord(**body, prev_hash=prev, event_hash="")
        # Hash the canonical serialized payload so verification over
        # model_dump(mode="json") recomputes the identical digest.
        record.event_hash = event_payload_hash(record.model_dump(mode="json"), prev)
        self._outbound.append(record)
        self._publish_pushed(record)
        return record

    def _publish_pushed(self, record: OutboundRecord) -> None:
        """Emit ``ng.sos.erp.journal_pushed`` on the event bus (best effort)."""
        if self._bus is None or record.receipt is None:
            return

        class _Pushed(BaseModel):
            state_id: str
            entry_id: str
            source_event_id: str
            entry_hash: str
            backend: str
            receipt_id: str
            event_hash: str
            timestamp: str

        payload = _Pushed(
            state_id=record.tenant_state_id,
            entry_id=record.entry_id,
            source_event_id=record.source_event_id,
            entry_hash=record.entry_hash or "",
            backend=record.receipt.backend.value,
            receipt_id=record.receipt.receipt_id,
            event_hash=record.event_hash,
            timestamp=record.timestamp.isoformat(),
        )
        try:
            self._bus.publish("ng.sos.erp.journal_pushed", payload)
        except Exception:  # noqa: BLE001 — publication must never break a push
            pass

    def outbound_log(self, state: Optional[str] = None) -> List[OutboundRecord]:
        if state is None:
            return list(self._outbound)
        require_state(state)
        return [r for r in self._outbound if r.tenant_state_id == state]

    def verify_outbound_log(self) -> List[str]:
        return verify_event_chain(
            [r.model_dump(mode="json") for r in self._outbound]
        )

    # ---------------- retry / dead-letter ----------------

    def _enqueue_retry(self, entry: JournalEntry, error: str) -> None:
        self._retry.append(
            RetryItem(
                entry=entry,
                attempts=1,
                next_attempt_at=utcnow() + timedelta(seconds=self.backoff_base_s),
                last_error=error,
            )
        )

    def process_retries(self) -> List[OutboundRecord]:
        """Attempt due retries; dead-letter after ``max_attempts``."""
        records: List[OutboundRecord] = []
        pending: List[RetryItem] = []
        now = utcnow()
        for item in self._retry:
            if item.next_attempt_at > now:
                pending.append(item)
                continue
            try:
                receipt = self.adapter.push_journal(
                    item.entry, self._coa[item.entry.tenant_state_id].mapping
                )
            except ErpPushError as exc:
                item.attempts += 1
                item.last_error = str(exc)
                if item.attempts >= self.max_attempts:
                    self._dead_letter.append(item.entry)
                    records.append(
                        self._record(item.entry, status=PushStatus.DEAD_LETTERED, receipt=None)
                    )
                else:
                    item.next_attempt_at = now + timedelta(
                        seconds=self.backoff_base_s * (2 ** (item.attempts - 1))
                    )
                    pending.append(item)
                continue
            records.append(
                self._record(item.entry, status=PushStatus.PUSHED, receipt=receipt)
            )
        self._retry = pending
        return records

    # ---------------- health ----------------

    def health(self) -> Dict[str, object]:
        return {
            "status": "ok",
            "service": "mod-erp-bridge",
            "adapter": type(self.adapter).__name__,
            "adapter_health": bool(self.adapter.health()),
            "retry_depth": len(self._retry),
            "dead_letters": len(self._dead_letter),
        }


def build_service(
    env: Optional[Dict[str, str]] = None,
    adapter: Optional[ErpAdapter] = None,
    bus: Optional[object] = None,
) -> ErpBridgeService:
    """Fail-closed factory: ERP_BACKEND=odoo/erpnext without config raises."""
    env = dict(os.environ if env is None else env)
    if adapter is None:
        if bus is None and env.get("EVENT_BUS", "memory") == "kafka":
            from _shared.eventbus import KafkaEventBus

            bus = KafkaEventBus()
        adapter = build_adapter(env)
    return ErpBridgeService(adapter=adapter, bus=bus)
