"""Domain: commodity aggregation, agro-hub warehouse receipts, crop traceability.

All state is tenant-scoped (``X-State-Tenant`` header). Warehouse receipts
(WRs) are hash-chained (services/_shared/hashchain, P1 audit immutability)
and follow the lifecycle ``issued -> pledged -> released`` with redemption
possible from ``issued``/``pledged``/``released``. Title transfers are
recorded as double-entry style ledger events (debit old holder / credit new
holder). Lot provenance is a hash-chained hop sequence
(farm -> aggregation center -> warehouse -> processor/export).

Production wiring: PostGIS geo validation, Temporal workflows for WR
lifecycle, Mojaloop settlement for pledge/redemption intents; this reference
build keeps deterministic in-memory state behind the same seams.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

# --- hash-chained audit: reuse services/_shared/hashchain, local fallback ----
try:
    from _shared.hashchain import GENESIS_PREV_HASH, event_payload_hash, verify_event_chain
except ImportError:  # minimal container images ship only the app package
    import hashlib
    import json

    GENESIS_PREV_HASH = "0" * 64

    def event_payload_hash(payload, prev_hash):  # type: ignore[no-redef]
        body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
        body["prev_hash"] = prev_hash
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def verify_event_chain(events):  # type: ignore[no-redef]
        errors: list[str] = []
        last = None
        for i, event in enumerate(events):
            prev = event.get("prev_hash")
            expected = GENESIS_PREV_HASH if last is None else last
            if prev != expected:
                errors.append(f"event {i}: broken chain link")
            if event.get("event_hash") != event_payload_hash(event, prev or ""):
                errors.append(f"event {i}: hash mismatch — record tampered")
            last = event.get("event_hash")
        return errors


#: Settlement/intent event topics (AsyncAPI channel names; Mojaloop seam).
EVENT_RECEIPT_ISSUED = "ng.sos.agri.warehouse_receipt_issued"
EVENT_LOT_TRACED_HOP = "ng.sos.agri.lot_traced_hop"
EVENT_RECEIPT_PLEDGED = "ng.sos.agri.receipt_pledged"
EVENT_RECEIPT_REDEEMED = "ng.sos.agri.receipt_redeemed"

#: Rough Nigeria bounding box (PostGIS-style sanity check for lot/hop geo).
NIGERIA_BBOX = (4.0, 2.0, 14.0, 15.0)  # (min_lat, min_lon, max_lat, max_lon)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def _validate_geo(latitude: float, longitude: float) -> None:
    """PostGIS-style point validation: in-range and inside the Nigeria bbox."""
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        raise ValueError(f"invalid coordinates ({latitude}, {longitude})")
    lo_lat, lo_lon, hi_lat, hi_lon = NIGERIA_BBOX
    if not (lo_lat <= latitude <= hi_lat and lo_lon <= longitude <= hi_lon):
        raise ValueError(
            f"coordinates ({latitude}, {longitude}) outside the Nigeria "
            "operational bounding box"
        )


class CrossTenantError(ValueError):
    """Tenant-isolation violation — mapped to HTTP 403 at the API layer
    (fail-closed, matching mod-police-cad / mod-safecity-vision)."""


class InvalidTransitionError(ValueError):
    """Illegal warehouse-receipt lifecycle transition — mapped to HTTP 409."""


class QualityGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class ReceiptStatus(str, Enum):
    ISSUED = "issued"
    PLEDGED = "pledged"
    RELEASED = "released"
    REDEEMED = "redeemed"


#: Legal WR lifecycle transitions (issued -> pledged -> released/redeemed).
WR_TRANSITIONS: dict[ReceiptStatus, set[ReceiptStatus]] = {
    ReceiptStatus.ISSUED: {ReceiptStatus.PLEDGED, ReceiptStatus.REDEEMED},
    ReceiptStatus.PLEDGED: {ReceiptStatus.RELEASED, ReceiptStatus.REDEEMED},
    ReceiptStatus.RELEASED: {ReceiptStatus.REDEEMED},
    ReceiptStatus.REDEEMED: set(),
}


class TraceStage(str, Enum):
    FARM = "farm"
    AGGREGATION_CENTER = "aggregation_center"
    WAREHOUSE = "warehouse"
    PROCESSOR = "processor"
    EXPORT = "export"


class Farmer(BaseModel):
    farmer_id: str
    tenant_state_id: str
    name: str
    kyc_ref: str = Field(..., description="Seam to mod-kyc-kyb identity record")
    lga: str = ""
    phone: str = ""
    registered_at: str


class Lot(BaseModel):
    lot_id: str
    tenant_state_id: str
    farmer_id: str
    commodity: str
    weight_kg: float = Field(..., gt=0)
    grade: QualityGrade
    moisture_pct: float = Field(..., ge=0, le=100)
    latitude: float
    longitude: float
    created_at: str
    stored: bool = False  # True once a warehouse receipt covers the lot


class Warehouse(BaseModel):
    warehouse_id: str
    tenant_state_id: str
    name: str
    lga: str = ""
    capacity_kg: float = Field(..., gt=0)
    latitude: float
    longitude: float
    registered_at: str


class WarehouseReceipt(BaseModel):
    receipt_id: str
    tenant_state_id: str
    warehouse_id: str
    lot_id: str
    holder_id: str  # current title holder (farmer_id or buyer/holder ref)
    quantity_kg: float = Field(..., gt=0)
    grade: QualityGrade
    storage_fees_kobo: int = Field(..., ge=0, description="Integer kobo (NGN minor unit)")
    status: ReceiptStatus
    pledgee_ref: str = ""  # lender/collateral agent while pledged
    issued_at: str
    updated_at: str


class TraceHop(BaseModel):
    hop_id: str
    lot_id: str
    tenant_state_id: str
    stage: TraceStage
    actor: str
    latitude: float
    longitude: float
    note: str = ""
    occurred_at: str
    prev_hash: str
    event_hash: str


# --- event payloads (published to the shared event bus) ----------------------


class ReceiptIssuedEvent(BaseModel):
    tenant_state_id: str
    receipt_id: str
    warehouse_id: str
    lot_id: str
    holder_id: str
    quantity_kg: float
    grade: str
    storage_fees_kobo: int
    event_hash: str
    issued_at: str


class ReceiptPledgedEvent(BaseModel):
    """Settlement intent: WR pledged as collateral (Mojaloop hook seam)."""

    tenant_state_id: str
    receipt_id: str
    lot_id: str
    holder_id: str
    pledgee_ref: str
    quantity_kg: float
    pledged_at: str


class ReceiptRedeemedEvent(BaseModel):
    """Settlement intent: WR redeemed/released (Mojaloop hook seam)."""

    tenant_state_id: str
    receipt_id: str
    lot_id: str
    holder_id: str
    outcome: str  # redeemed | released
    occurred_at: str


class LotTracedHopEvent(BaseModel):
    tenant_state_id: str
    lot_id: str
    hop_id: str
    stage: str
    actor: str
    event_hash: str
    occurred_at: str


#: Commodity catalog per state (National Edition blueprint fixtures); any
#: other tenant falls back to the generic smallholder list.
COMMODITY_CATALOG: dict[str, list[str]] = {
    "benue": ["yam"],
    "osun": ["cocoa"],
    "taraba": ["tea"],
    "kebbi": ["rice"],
    "kano": ["sorghum", "maize"],
}

GENERIC_COMMODITIES = ["maize", "cassava", "soybeans", "millet", "groundnut"]


def catalog_for(tenant_state_id: str) -> list[str]:
    return list(COMMODITY_CATALOG.get(tenant_state_id, GENERIC_COMMODITIES))


class TenantState:
    """Per-tenant slice of the store (hard isolation between states)."""

    def __init__(self, tenant_state_id: str) -> None:
        self.tenant_state_id = tenant_state_id
        self.farmers: dict[str, Farmer] = {}
        self.lots: dict[str, Lot] = {}
        self.warehouses: dict[str, Warehouse] = {}
        self.receipts: dict[str, WarehouseReceipt] = {}
        self.receipt_chain: list[dict] = []  # hash-chained WR lifecycle events
        self.title_ledger: list[dict] = []  # double-entry title-transfer records
        self.trace_hops: dict[str, list[TraceHop]] = {}  # lot_id -> hops


class AgriStore:
    """Thread-safe tenant-scoped store (keyed by ``X-State-Tenant``).

    Production: PostGIS for geo, Temporal for WR lifecycle workflows,
    TigerBeetle-style double-entry title ledger, Kafka via _shared.eventbus.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenants: dict[str, TenantState] = {}

    def tenant(self, tenant_state_id: str) -> TenantState:
        with self._lock:
            state = self._tenants.get(tenant_state_id)
            if state is None:
                state = TenantState(tenant_state_id)
                self._tenants[tenant_state_id] = state
            return state

    # --- hash-chain helper ----------------------------------------------------
    def _append_receipt_event(self, state: TenantState, kind: str,
                              payload: dict) -> dict:
        body = {
            "event_id": _id("wr-ev"),
            "kind": kind,
            "tenant_state_id": state.tenant_state_id,
            "recorded_at": _now(),
            **payload,
        }
        prev = state.receipt_chain[-1]["event_hash"] if state.receipt_chain else GENESIS_PREV_HASH
        body["prev_hash"] = prev
        body["event_hash"] = event_payload_hash(body, prev)
        state.receipt_chain.append(body)
        return body

    def verify_receipt_chain(self, tenant_state_id: str) -> list[str]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            chain = [dict(e) for e in state.receipt_chain]
        return verify_event_chain(chain)

    # --- farmer registry --------------------------------------------------------
    def register_farmer(self, tenant_state_id: str, name: str, kyc_ref: str,
                        lga: str = "", phone: str = "") -> Farmer:
        farmer = Farmer(farmer_id=_id("farmer"), tenant_state_id=tenant_state_id,
                        name=name, kyc_ref=kyc_ref, lga=lga, phone=phone,
                        registered_at=_now())
        state = self.tenant(tenant_state_id)
        with self._lock:
            state.farmers[farmer.farmer_id] = farmer
        return farmer

    def list_farmers(self, tenant_state_id: str) -> list[Farmer]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            return list(state.farmers.values())

    def get_farmer(self, tenant_state_id: str, farmer_id: str) -> Farmer:
        state = self.tenant(tenant_state_id)
        farmer = state.farmers.get(farmer_id)
        if farmer is None:
            raise KeyError(f"farmer '{farmer_id}' not found")
        return farmer

    # --- aggregation intake -----------------------------------------------------
    def intake_lot(self, tenant_state_id: str, farmer_id: str, commodity: str,
                   weight_kg: float, grade: QualityGrade, moisture_pct: float,
                   latitude: float, longitude: float) -> Lot:
        """Record an aggregation lot and auto-trace its ``farm`` origin hop."""
        _validate_geo(latitude, longitude)
        state = self.tenant(tenant_state_id)
        with self._lock:
            if farmer_id not in state.farmers:
                raise KeyError(f"farmer '{farmer_id}' not found")
            lot = Lot(lot_id=_id("lot"), tenant_state_id=tenant_state_id,
                      farmer_id=farmer_id, commodity=commodity.lower(),
                      weight_kg=weight_kg, grade=grade, moisture_pct=moisture_pct,
                      latitude=latitude, longitude=longitude, created_at=_now())
            state.lots[lot.lot_id] = lot
        self.add_trace_hop(tenant_state_id, lot.lot_id, TraceStage.FARM,
                           actor=farmer_id, latitude=latitude,
                           longitude=longitude, note="farm origin (intake)")
        return lot

    def list_lots(self, tenant_state_id: str) -> list[Lot]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            return list(state.lots.values())

    def get_lot(self, tenant_state_id: str, lot_id: str) -> Lot:
        state = self.tenant(tenant_state_id)
        lot = state.lots.get(lot_id)
        if lot is None:
            raise KeyError(f"lot '{lot_id}' not found")
        return lot

    # --- warehouses --------------------------------------------------------------
    def register_warehouse(self, tenant_state_id: str, name: str, lga: str,
                           capacity_kg: float, latitude: float,
                           longitude: float) -> Warehouse:
        _validate_geo(latitude, longitude)
        wh = Warehouse(warehouse_id=_id("wh"), tenant_state_id=tenant_state_id,
                       name=name, lga=lga, capacity_kg=capacity_kg,
                       latitude=latitude, longitude=longitude, registered_at=_now())
        state = self.tenant(tenant_state_id)
        with self._lock:
            state.warehouses[wh.warehouse_id] = wh
        return wh

    def list_warehouses(self, tenant_state_id: str) -> list[Warehouse]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            return list(state.warehouses.values())

    # --- warehouse receipts -------------------------------------------------------
    def issue_receipt(self, tenant_state_id: str, warehouse_id: str, lot_id: str,
                      storage_fees_kobo: int) -> tuple[WarehouseReceipt, ReceiptIssuedEvent]:
        """Issue a signed (hash-chained) warehouse receipt for a stored lot."""
        state = self.tenant(tenant_state_id)
        with self._lock:
            lot = state.lots.get(lot_id)
            if lot is None:
                raise KeyError(f"lot '{lot_id}' not found")
            if warehouse_id not in state.warehouses:
                raise KeyError(f"warehouse '{warehouse_id}' not found")
            if lot.stored:
                raise InvalidTransitionError(
                    f"lot '{lot_id}' is already covered by a warehouse receipt"
                )
            now = _now()
            receipt = WarehouseReceipt(
                receipt_id=_id("wr"), tenant_state_id=tenant_state_id,
                warehouse_id=warehouse_id, lot_id=lot_id, holder_id=lot.farmer_id,
                quantity_kg=lot.weight_kg, grade=lot.grade,
                storage_fees_kobo=storage_fees_kobo, status=ReceiptStatus.ISSUED,
                issued_at=now, updated_at=now,
            )
            state.receipts[receipt.receipt_id] = receipt
            lot.stored = True
            event = self._append_receipt_event(state, "issued", {
                "receipt_id": receipt.receipt_id, "warehouse_id": warehouse_id,
                "lot_id": lot_id, "holder_id": receipt.holder_id,
                "quantity_kg": receipt.quantity_kg, "grade": receipt.grade.value,
                "storage_fees_kobo": storage_fees_kobo,
            })
        wh = state.warehouses[warehouse_id]
        self.add_trace_hop(tenant_state_id, lot_id, TraceStage.WAREHOUSE,
                           actor=warehouse_id, latitude=wh.latitude,
                           longitude=wh.longitude, note=f"stored at {wh.name}")
        issued = ReceiptIssuedEvent(
            tenant_state_id=tenant_state_id, receipt_id=receipt.receipt_id,
            warehouse_id=warehouse_id, lot_id=lot_id, holder_id=receipt.holder_id,
            quantity_kg=receipt.quantity_kg, grade=receipt.grade.value,
            storage_fees_kobo=storage_fees_kobo, event_hash=event["event_hash"],
            issued_at=receipt.issued_at,
        )
        return receipt, issued

    def get_receipt(self, tenant_state_id: str, receipt_id: str) -> WarehouseReceipt:
        state = self.tenant(tenant_state_id)
        receipt = state.receipts.get(receipt_id)
        if receipt is None:
            raise KeyError(f"receipt '{receipt_id}' not found")
        return receipt

    def list_receipts(self, tenant_state_id: str) -> list[WarehouseReceipt]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            return list(state.receipts.values())

    def _transition(self, state: TenantState, receipt_id: str,
                    target: ReceiptStatus, kind: str,
                    extra: dict | None = None) -> tuple[WarehouseReceipt, dict]:
        receipt = state.receipts.get(receipt_id)
        if receipt is None:
            raise KeyError(f"receipt '{receipt_id}' not found")
        if target not in WR_TRANSITIONS[receipt.status]:
            raise InvalidTransitionError(
                f"receipt '{receipt_id}' cannot transition "
                f"{receipt.status.value} -> {target.value}"
            )
        receipt.status = target
        receipt.updated_at = _now()
        if extra and "pledgee_ref" in extra:
            receipt.pledgee_ref = extra["pledgee_ref"]
        event = self._append_receipt_event(state, kind, {
            "receipt_id": receipt_id, "warehouse_id": receipt.warehouse_id,
            "lot_id": receipt.lot_id, "holder_id": receipt.holder_id,
            "quantity_kg": receipt.quantity_kg, **(extra or {}),
        })
        return receipt, event

    def pledge_receipt(self, tenant_state_id: str, receipt_id: str,
                       pledgee_ref: str) -> tuple[WarehouseReceipt, ReceiptPledgedEvent]:
        """Pledge a WR as loan collateral (settlement intent emitted)."""
        state = self.tenant(tenant_state_id)
        with self._lock:
            receipt, _ = self._transition(state, receipt_id, ReceiptStatus.PLEDGED,
                                          "pledged", {"pledgee_ref": pledgee_ref})
        event = ReceiptPledgedEvent(
            tenant_state_id=tenant_state_id, receipt_id=receipt_id,
            lot_id=receipt.lot_id, holder_id=receipt.holder_id,
            pledgee_ref=pledgee_ref, quantity_kg=receipt.quantity_kg,
            pledged_at=receipt.updated_at,
        )
        return receipt, event

    def _release_or_redeem(self, tenant_state_id: str, receipt_id: str,
                           target: ReceiptStatus) -> tuple[WarehouseReceipt, ReceiptRedeemedEvent]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            receipt, _ = self._transition(state, receipt_id, target, target.value)
        event = ReceiptRedeemedEvent(
            tenant_state_id=tenant_state_id, receipt_id=receipt_id,
            lot_id=receipt.lot_id, holder_id=receipt.holder_id,
            outcome=target.value, occurred_at=receipt.updated_at,
        )
        return receipt, event

    def release_receipt(self, tenant_state_id: str,
                        receipt_id: str) -> tuple[WarehouseReceipt, ReceiptRedeemedEvent]:
        """Release a pledged WR (collateral claim lifted)."""
        return self._release_or_redeem(tenant_state_id, receipt_id, ReceiptStatus.RELEASED)

    def redeem_receipt(self, tenant_state_id: str,
                       receipt_id: str) -> tuple[WarehouseReceipt, ReceiptRedeemedEvent]:
        """Redeem a WR — goods leave the warehouse (settlement intent emitted)."""
        return self._release_or_redeem(tenant_state_id, receipt_id, ReceiptStatus.REDEEMED)

    def transfer_title(self, tenant_state_id: str, receipt_id: str, from_holder: str,
                       to_holder: str) -> WarehouseReceipt:
        """Transfer WR title between holders; records a double-entry style
        ledger pair (DEBIT from-holder / CREDIT to-holder), both hash-chained."""
        if from_holder == to_holder:
            raise ValueError("transfer to the same holder is a no-op and is rejected")
        state = self.tenant(tenant_state_id)
        with self._lock:
            receipt = state.receipts.get(receipt_id)
            if receipt is None:
                raise KeyError(f"receipt '{receipt_id}' not found")
            if receipt.status is ReceiptStatus.REDEEMED:
                raise InvalidTransitionError(
                    f"receipt '{receipt_id}' is redeemed; title is no longer negotiable"
                )
            if receipt.holder_id != from_holder:
                raise InvalidTransitionError(
                    f"holder mismatch: receipt '{receipt_id}' is held by "
                    f"'{receipt.holder_id}', not '{from_holder}'"
                )
            transfer_id = _id("xfr")
            base = {
                "transfer_id": transfer_id,
                "tenant_state_id": tenant_state_id,
                "receipt_id": receipt_id,
                "quantity_kg": receipt.quantity_kg,
                "recorded_at": _now(),
            }
            prev = state.title_ledger[-1]["event_hash"] if state.title_ledger else GENESIS_PREV_HASH
            debit = {**base, "entry_type": "debit", "holder_id": from_holder}
            debit["prev_hash"] = prev
            debit["event_hash"] = event_payload_hash(debit, prev)
            credit = {**base, "entry_type": "credit", "holder_id": to_holder}
            credit["prev_hash"] = debit["event_hash"]
            credit["event_hash"] = event_payload_hash(credit, debit["event_hash"])
            state.title_ledger.extend([debit, credit])
            receipt.holder_id = to_holder
            receipt.updated_at = _now()
            self._append_receipt_event(state, "title_transferred", {
                "receipt_id": receipt_id, "warehouse_id": receipt.warehouse_id,
                "lot_id": receipt.lot_id, "from_holder": from_holder,
                "holder_id": to_holder, "transfer_id": transfer_id,
                "quantity_kg": receipt.quantity_kg,
            })
        return receipt

    def title_ledger_for(self, tenant_state_id: str, receipt_id: str) -> list[dict]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            entries = [dict(e) for e in state.title_ledger
                       if e["receipt_id"] == receipt_id]
        return entries

    def verify_title_ledger(self, tenant_state_id: str) -> list[str]:
        state = self.tenant(tenant_state_id)
        with self._lock:
            entries = [dict(e) for e in state.title_ledger]
        return verify_event_chain(entries)

    # --- crop traceability ---------------------------------------------------------
    def add_trace_hop(self, tenant_state_id: str, lot_id: str, stage: TraceStage,
                      actor: str, latitude: float, longitude: float,
                      note: str = "") -> tuple[TraceHop, LotTracedHopEvent]:
        """Append a provenance hop to a lot's hash-chained trace."""
        _validate_geo(latitude, longitude)
        state = self.tenant(tenant_state_id)
        with self._lock:
            if lot_id not in state.lots:
                raise KeyError(f"lot '{lot_id}' not found")
            chain = state.trace_hops.setdefault(lot_id, [])
            prev = chain[-1].event_hash if chain else GENESIS_PREV_HASH
            body = {
                "hop_id": _id("hop"), "lot_id": lot_id,
                "tenant_state_id": tenant_state_id, "stage": stage.value,
                "actor": actor, "latitude": latitude, "longitude": longitude,
                "note": note, "occurred_at": _now(),
            }
            hop = TraceHop(**body, prev_hash=prev,
                           event_hash=event_payload_hash(body, prev))
            chain.append(hop)
        event = LotTracedHopEvent(
            tenant_state_id=tenant_state_id, lot_id=lot_id, hop_id=hop.hop_id,
            stage=stage.value, actor=actor, event_hash=hop.event_hash,
            occurred_at=hop.occurred_at,
        )
        return hop, event

    def trace(self, tenant_state_id: str, lot_id: str) -> dict:
        """Full provenance chain for a lot plus hash-chain verification."""
        state = self.tenant(tenant_state_id)
        with self._lock:
            if lot_id not in state.lots:
                raise KeyError(f"lot '{lot_id}' not found")
            hops = [h.model_dump() for h in state.trace_hops.get(lot_id, [])]
        errors = verify_event_chain(hops)
        return {
            "lot_id": lot_id,
            "tenant_state_id": tenant_state_id,
            "hop_count": len(hops),
            "hops": hops,
            "chain_valid": not errors,
            "errors": errors,
        }
