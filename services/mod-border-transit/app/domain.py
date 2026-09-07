"""Domain model for mod-border-transit (National Edition blueprint).

Cross-Border Cargo RFID Tracking & Transit Telematics for the six adoption
states (Taraba, Borno, Katsina, Sokoto, Ogun, Cross River). All state is
tenant-scoped: the API layer resolves ``X-State-Tenant`` and every store
method takes ``tenant_state_id`` as its first argument.

Capabilities:

* Border crossing registry (per-state fixture defaults, CRUD).
* Transit consignments — declared → sealed → in_transit → arrived → cleared
  lifecycle with invalid transitions rejected (409 at the API layer).
* RFID checkpoint scan events appended to the consignment; tamper alerts on
  broken seals or route deviation vs the declared ordered corridor.
* Transit telematics — GPS ping ingest, geofence corridor check
  (point-in-polygon ray-cast), deviation events.
* Transit levy — flat fee + ad-valorem basis points on the declared value,
  integer-kobo arithmetic only, recorded as a ledger-style double-entry
  intent (TigerBeetle-ready) and published on the shared event bus.
* Hash-chained audit of clearance decisions (services/_shared/hashchain).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

# Shared canonical-JSON/SHA-256 hash-chain helpers (services/_shared).
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
        return _hashlib.sha256(_canonical(body).encode()).hexdigest()

    def verify_event_chain(events):  # noqa: ANN001, ANN201, ANN202
        errors: list[str] = []
        last_hash: str | None = None
        for index, event in enumerate(events):
            expected_prev = GENESIS_PREV_HASH if last_hash is None else last_hash
            prev = event.get("prev_hash")
            if prev != expected_prev:
                errors.append(f"index {index}: broken chain link")
            recomputed = event_payload_hash(event, prev or "")
            if event.get("event_hash") != recomputed:
                errors.append(f"index {index}: hash mismatch — record tampered")
            last_hash = event.get("event_hash")
        return errors


# --------------------------------------------------------------------------
# Event bus seam (InMemoryEventBus default; Kafka/Fluvio via _shared/eventbus)
# --------------------------------------------------------------------------


class EventBus(Protocol):
    def publish(self, topic: str, payload: BaseModel) -> None: ...


class _NullEventBus:
    """Used only when no bus is injected (unit tests of pure domain math)."""

    def __init__(self) -> None:
        self.published: List[dict] = []

    def publish(self, topic: str, payload: BaseModel) -> None:
        self.published.append(
            {"topic": topic, "payload": payload.model_dump(mode="json")}
        )


#: Published topics (AsyncAPI channel names).
TOPIC_CROSSING_RECORDED = "ng.sos.border.transit_crossing_recorded"
TOPIC_TAMPER_ALERT = "ng.sos.border.tamper_alert"
TOPIC_LEVY_ASSESSED = "ng.sos.border.levy_assessed"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class InvalidTransitionError(ValueError):
    """Raised on an illegal consignment lifecycle transition (HTTP 409)."""


class CrossTenantError(PermissionError):
    """Raised when an id owned by another tenant is accessed (HTTP 403)."""


# --------------------------------------------------------------------------
# Fixture reference data
# --------------------------------------------------------------------------


class Crossing(BaseModel):
    crossing_id: str
    tenant_state_id: str
    name: str
    neighbor_country: str
    latitude: float
    longitude: float


#: Ordered transit corridor checkpoints per tenant (checkpoint ids must match
#: crossing ids or named inland checkpoints). Used for route-deviation
#: detection: scans must appear in corridor order.
FIXTURE_CORRIDORS: Dict[str, List[str]] = {
    "taraba": ["gembu-cameroon", "ibi"],
    "borno": ["gamboru-chad", "banki"],
    "katsina": ["jibia-niger", "daura"],
    "sokoto": ["illela-niger"],
    "ogun": ["idiroko-benin"],
    "cross_river": ["mfum-cameroon"],
}

FIXTURE_CROSSINGS: Dict[str, List[dict]] = {
    "taraba": [
        {"crossing_id": "gembu-cameroon", "name": "Gembu–Cameroon",
         "neighbor_country": "Cameroon", "latitude": 6.7167, "longitude": 11.2667},
        {"crossing_id": "ibi", "name": "Ibi",
         "neighbor_country": "internal", "latitude": 8.1833, "longitude": 9.7500},
    ],
    "borno": [
        {"crossing_id": "gamboru-chad", "name": "Gamboru–Chad",
         "neighbor_country": "Chad", "latitude": 12.3667, "longitude": 14.2000},
        {"crossing_id": "banki", "name": "Banki",
         "neighbor_country": "Cameroon", "latitude": 11.9333, "longitude": 14.1333},
    ],
    "katsina": [
        {"crossing_id": "jibia-niger", "name": "Jibia–Niger",
         "neighbor_country": "Niger", "latitude": 13.0833, "longitude": 7.2333},
        {"crossing_id": "daura", "name": "Daura",
         "neighbor_country": "Niger", "latitude": 13.0333, "longitude": 8.3167},
    ],
    "sokoto": [
        {"crossing_id": "illela-niger", "name": "Illela–Niger",
         "neighbor_country": "Niger", "latitude": 13.7333, "longitude": 5.3000},
    ],
    "ogun": [
        {"crossing_id": "idiroko-benin", "name": "Idiroko–Benin",
         "neighbor_country": "Benin", "latitude": 6.8500, "longitude": 2.7333},
    ],
    "cross_river": [
        {"crossing_id": "mfum-cameroon", "name": "Mfum–Cameroon",
         "neighbor_country": "Cameroon", "latitude": 6.0500, "longitude": 9.0500},
    ],
}

#: Geofence corridor polygons per tenant (lon/lat vertex rings, ray-cast
#: tested). Fixtures are generous bounding boxes around the transit corridor.
FIXTURE_CORRIDOR_POLYGONS: Dict[str, List[List[float]]] = {
    "taraba": [[9.0, 6.0], [12.0, 6.0], [12.0, 8.5], [9.0, 8.5]],
    "borno": [[13.5, 11.5], [14.5, 11.5], [14.5, 12.8], [13.5, 12.8]],
    "katsina": [[7.0, 12.7], [8.6, 12.7], [8.6, 13.4], [7.0, 13.4]],
    "sokoto": [[4.9, 13.4], [5.6, 13.4], [5.6, 14.0], [4.9, 14.0]],
    "ogun": [[2.4, 6.5], [3.1, 6.5], [3.1, 7.2], [2.4, 7.2]],
    "cross_river": [[8.7, 5.7], [9.4, 5.7], [9.4, 6.4], [8.7, 6.4]],
}

#: Per-state transit levy policy: flat fee + ad-valorem basis points on the
#: declared value. Integer kobo arithmetic only.
FIXTURE_LEVY_POLICIES: Dict[str, dict] = {
    "taraba": {"flat_fee_kobo": 250_000, "ad_valorem_bps": 50},
    "borno": {"flat_fee_kobo": 250_000, "ad_valorem_bps": 50},
    "katsina": {"flat_fee_kobo": 200_000, "ad_valorem_bps": 40},
    "sokoto": {"flat_fee_kobo": 200_000, "ad_valorem_bps": 40},
    "ogun": {"flat_fee_kobo": 350_000, "ad_valorem_bps": 75},
    "cross_river": {"flat_fee_kobo": 300_000, "ad_valorem_bps": 60},
}

DEFAULT_LEVY_POLICY = {"flat_fee_kobo": 200_000, "ad_valorem_bps": 50}


class LevyPolicy(BaseModel):
    tenant_state_id: str
    flat_fee_kobo: int = Field(..., ge=0)
    ad_valorem_bps: int = Field(..., ge=0, le=10_000)


# --------------------------------------------------------------------------
# Consignments, scans, telematics, levy, audit
# --------------------------------------------------------------------------

CONSIGNMENT_STATES = ("declared", "sealed", "in_transit", "arrived", "cleared")

#: Legal lifecycle edges; everything else is InvalidTransitionError (409).
_ALLOWED_TRANSITIONS = {
    "declared": {"sealed"},
    "sealed": {"in_transit"},
    "in_transit": {"arrived"},
    "arrived": {"cleared"},
    "cleared": set(),
}


class Consignment(BaseModel):
    consignment_id: str
    tenant_state_id: str
    trader_ref: str
    rfid_tag_id: str
    goods_description: str
    hs_code: str
    declared_value_kobo: int = Field(..., ge=0)
    origin_crossing_id: str
    destination_crossing_id: str
    corridor: List[str]
    state: str = "declared"
    seal_intact: bool = True
    scans: List["ScanEvent"] = []
    declared_at: str


class ScanEvent(BaseModel):
    scan_id: str
    consignment_id: str
    tenant_state_id: str
    rfid_tag_id: str
    checkpoint_id: str
    direction: str = Field(..., pattern="^(entry|exit)$")
    scanned_at: str
    latitude: float
    longitude: float
    seal_intact: bool = True


class TamperAlert(BaseModel):
    alert_id: str
    tenant_state_id: str
    consignment_id: str
    kind: str  # seal_broken | route_deviation | geofence_deviation
    detail: str
    raised_at: str


class TelematicsPing(BaseModel):
    ping_id: str
    tenant_state_id: str
    truck_id: str
    latitude: float
    longitude: float
    speed_kph: float = Field(..., ge=0)
    pinged_at: str
    in_corridor: bool


class LedgerEntry(BaseModel):
    """Ledger-style double-entry intent (TigerBeetle-ready transfer pair)."""

    entry_id: str
    tenant_state_id: str
    consignment_id: str
    debit_account: str
    credit_account: str
    amount_kobo: int = Field(..., ge=0)
    memo: str


class LevyAssessment(BaseModel):
    assessment_id: str
    tenant_state_id: str
    consignment_id: str
    flat_fee_kobo: int
    ad_valorem_bps: int
    ad_valorem_kobo: int
    total_kobo: int
    ledger_entries: List[LedgerEntry]
    assessed_at: str


class ClearanceAuditRecord(BaseModel):
    event_id: str
    tenant_state_id: str
    consignment_id: str
    decision: str  # cleared
    officer_ref: str
    decided_at: str
    prev_hash: str
    event_hash: str


def _now_iso(clock) -> str:
    return clock()


def point_in_polygon(latitude: float, longitude: float,
                     polygon: List[List[float]]) -> bool:
    """Ray-cast point-in-polygon over a lon/lat vertex ring.

    ``polygon`` vertices are ``[lon, lat]`` pairs (GeoJSON order). A point
    exactly on a vertex/edge counts as inside-adjacent (returns True by the
    standard even-odd rule for the fixtures used here).
    """
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    x, y = longitude, latitude
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


class BorderTransitStore:
    """In-memory tenant-scoped store for the reference implementation."""

    def __init__(self, bus: Optional[EventBus] = None, clock=None) -> None:
        from datetime import datetime, timezone

        self._clock = clock or (
            lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        self._bus: EventBus = bus or _NullEventBus()
        self._lock = threading.Lock()
        self.crossings: Dict[str, Crossing] = {}
        self.consignment_index: Dict[str, str] = {}  # id -> tenant
        self._consignments: Dict[str, Consignment] = {}
        self.corridors: Dict[str, List[str]] = {
            k: list(v) for k, v in FIXTURE_CORRIDORS.items()
        }
        self.corridor_polygons: Dict[str, List[List[float]]] = {
            k: [list(p) for p in v] for k, v in FIXTURE_CORRIDOR_POLYGONS.items()
        }
        self.levy_policies: Dict[str, LevyPolicy] = {
            tenant: LevyPolicy(tenant_state_id=tenant, **cfg)
            for tenant, cfg in FIXTURE_LEVY_POLICIES.items()
        }
        self.tamper_alerts: List[TamperAlert] = []
        self.telematics_pings: List[TelematicsPing] = []
        self.assessments: Dict[str, LevyAssessment] = {}
        self.clearance_chain: List[ClearanceAuditRecord] = []
        self._load_fixture_crossings()

    # -- fixtures ----------------------------------------------------------

    def _load_fixture_crossings(self) -> None:
        for tenant, rows in FIXTURE_CROSSINGS.items():
            for row in rows:
                self.crossings[row["crossing_id"]] = Crossing(
                    tenant_state_id=tenant, **row
                )

    # -- crossings CRUD -----------------------------------------------------

    def list_crossings(self, tenant_state_id: str) -> List[Crossing]:
        return [c for c in self.crossings.values()
                if c.tenant_state_id == tenant_state_id]

    def get_crossing(self, tenant_state_id: str, crossing_id: str) -> Crossing:
        crossing = self.crossings.get(crossing_id)
        if crossing is None:
            raise KeyError(f"crossing '{crossing_id}' not found")
        if crossing.tenant_state_id != tenant_state_id:
            raise CrossTenantError(
                f"crossing '{crossing_id}' belongs to another tenant state"
            )
        return crossing

    def create_crossing(self, tenant_state_id: str, crossing_id: str, name: str,
                        neighbor_country: str, latitude: float,
                        longitude: float) -> Crossing:
        with self._lock:
            if crossing_id in self.crossings:
                raise ValueError(f"crossing '{crossing_id}' already exists")
            crossing = Crossing(
                crossing_id=crossing_id, tenant_state_id=tenant_state_id,
                name=name, neighbor_country=neighbor_country,
                latitude=latitude, longitude=longitude,
            )
            self.crossings[crossing_id] = crossing
        return crossing

    def update_crossing(self, tenant_state_id: str, crossing_id: str,
                        **changes) -> Crossing:
        crossing = self.get_crossing(tenant_state_id, crossing_id)
        data = crossing.model_dump()
        for key, value in changes.items():
            if value is not None and key in data and key not in (
                "crossing_id", "tenant_state_id"
            ):
                data[key] = value
        updated = Crossing(**data)
        with self._lock:
            self.crossings[crossing_id] = updated
        return updated

    def delete_crossing(self, tenant_state_id: str, crossing_id: str) -> None:
        self.get_crossing(tenant_state_id, crossing_id)
        with self._lock:
            del self.crossings[crossing_id]

    # -- consignments --------------------------------------------------------

    def declare_consignment(
        self, tenant_state_id: str, trader_ref: str, rfid_tag_id: str,
        goods_description: str, hs_code: str, declared_value_kobo: int,
        origin_crossing_id: str, destination_crossing_id: str,
    ) -> Consignment:
        self.get_crossing(tenant_state_id, origin_crossing_id)
        self.get_crossing(tenant_state_id, destination_crossing_id)
        corridor = self.corridors.get(tenant_state_id, [])
        consignment = Consignment(
            consignment_id=f"con-{uuid4().hex[:10]}",
            tenant_state_id=tenant_state_id,
            trader_ref=trader_ref,
            rfid_tag_id=rfid_tag_id,
            goods_description=goods_description,
            hs_code=hs_code,
            declared_value_kobo=declared_value_kobo,
            origin_crossing_id=origin_crossing_id,
            destination_crossing_id=destination_crossing_id,
            corridor=list(corridor),
            declared_at=self._clock(),
        )
        with self._lock:
            self._consignments[consignment.consignment_id] = consignment
            self.consignment_index[consignment.consignment_id] = tenant_state_id
        self._bus.publish(TOPIC_CROSSING_RECORDED, consignment)
        return consignment

    def get_consignment(self, tenant_state_id: str,
                        consignment_id: str) -> Consignment:
        consignment = self._consignments.get(consignment_id)
        if consignment is None:
            raise KeyError(f"consignment '{consignment_id}' not found")
        if consignment.tenant_state_id != tenant_state_id:
            raise CrossTenantError(
                f"consignment '{consignment_id}' belongs to another tenant state"
            )
        return consignment

    def list_consignments(self, tenant_state_id: str,
                          state: Optional[str] = None) -> List[Consignment]:
        rows = [c for c in self._consignments.values()
                if c.tenant_state_id == tenant_state_id]
        if state is not None:
            rows = [c for c in rows if c.state == state]
        return rows

    def transition(self, tenant_state_id: str, consignment_id: str,
                   target: str) -> Consignment:
        consignment = self.get_consignment(tenant_state_id, consignment_id)
        allowed = _ALLOWED_TRANSITIONS.get(consignment.state, set())
        if target not in allowed:
            raise InvalidTransitionError(
                f"cannot transition consignment '{consignment_id}' from "
                f"'{consignment.state}' to '{target}'"
            )
        updated = consignment.model_copy(update={"state": target})
        with self._lock:
            self._consignments[consignment_id] = updated
        return updated

    # -- RFID scans -----------------------------------------------------------

    def record_scan(self, tenant_state_id: str, consignment_id: str,
                    checkpoint_id: str, direction: str, scanned_at: str,
                    latitude: float, longitude: float,
                    seal_intact: bool = True,
                    scanned_tag_id: Optional[str] = None) -> ScanEvent:
        consignment = self.get_consignment(tenant_state_id, consignment_id)
        if consignment.state in ("declared",):
            # First scan at the origin crossing moves declared→sealed? No —
            # sealing is an explicit act. Scans auto-advance sealed→in_transit.
            pass
        scan = ScanEvent(
            scan_id=f"scn-{uuid4().hex[:10]}",
            consignment_id=consignment_id,
            tenant_state_id=tenant_state_id,
            rfid_tag_id=scanned_tag_id or consignment.rfid_tag_id,
            checkpoint_id=checkpoint_id,
            direction=direction,
            scanned_at=scanned_at,
            latitude=latitude,
            longitude=longitude,
            seal_intact=seal_intact,
        )
        scans = list(consignment.scans) + [scan]
        updates: dict = {"scans": scans}
        if consignment.state == "sealed":
            updates["state"] = "in_transit"
        if not seal_intact:
            updates["seal_intact"] = False
        updated = consignment.model_copy(update=updates)
        with self._lock:
            self._consignments[consignment_id] = updated

        if not seal_intact:
            self._raise_tamper_alert(
                tenant_state_id, consignment_id, "seal_broken",
                f"seal reported broken at checkpoint '{checkpoint_id}'",
            )
        self._check_route_deviation(updated)
        return scan

    def _check_route_deviation(self, consignment: Consignment) -> None:
        """Ordered-checkpoint corridor validation: scanned checkpoints must be
        a subsequence of the declared corridor (in order, no repeats backward)."""
        corridor = consignment.corridor
        position = 0
        for scan in consignment.scans:
            cp = scan.checkpoint_id
            if cp not in corridor:
                self._raise_tamper_alert(
                    consignment.tenant_state_id, consignment.consignment_id,
                    "route_deviation",
                    f"scan at '{cp}' is off the declared corridor {corridor}",
                )
                return
            idx = corridor.index(cp)
            if idx < position:
                self._raise_tamper_alert(
                    consignment.tenant_state_id, consignment.consignment_id,
                    "route_deviation",
                    f"scan at '{cp}' is out of corridor order (regression)",
                )
                return
            position = idx

    def _raise_tamper_alert(self, tenant_state_id: str, consignment_id: str,
                            kind: str, detail: str) -> TamperAlert:
        alert = TamperAlert(
            alert_id=f"tam-{uuid4().hex[:10]}",
            tenant_state_id=tenant_state_id,
            consignment_id=consignment_id,
            kind=kind,
            detail=detail,
            raised_at=self._clock(),
        )
        with self._lock:
            self.tamper_alerts.append(alert)
        self._bus.publish(TOPIC_TAMPER_ALERT, alert)
        return alert

    def list_scans(self, tenant_state_id: str,
                   consignment_id: Optional[str] = None) -> List[ScanEvent]:
        scans: List[ScanEvent] = []
        for c in self._consignments.values():
            if c.tenant_state_id != tenant_state_id:
                continue
            if consignment_id and c.consignment_id != consignment_id:
                continue
            scans.extend(c.scans)
        return scans

    def list_tamper_alerts(self, tenant_state_id: str) -> List[TamperAlert]:
        return [a for a in self.tamper_alerts
                if a.tenant_state_id == tenant_state_id]

    # -- telematics -------------------------------------------------------------

    def ingest_ping(self, tenant_state_id: str, truck_id: str, latitude: float,
                    longitude: float, speed_kph: float,
                    pinged_at: str,
                    consignment_id: Optional[str] = None) -> TelematicsPing:
        polygon = self.corridor_polygons.get(tenant_state_id, [])
        in_corridor = point_in_polygon(latitude, longitude, polygon)
        ping = TelematicsPing(
            ping_id=f"png-{uuid4().hex[:10]}",
            tenant_state_id=tenant_state_id,
            truck_id=truck_id,
            latitude=latitude,
            longitude=longitude,
            speed_kph=speed_kph,
            pinged_at=pinged_at,
            in_corridor=in_corridor,
        )
        with self._lock:
            self.telematics_pings.append(ping)
        if not in_corridor:
            self._raise_tamper_alert(
                tenant_state_id,
                consignment_id or f"truck:{truck_id}",
                "geofence_deviation",
                f"truck '{truck_id}' pinged outside the transit corridor "
                f"geofence at ({latitude}, {longitude})",
            )
        return ping

    def list_pings(self, tenant_state_id: str,
                   truck_id: Optional[str] = None) -> List[TelematicsPing]:
        pings = [p for p in self.telematics_pings
                 if p.tenant_state_id == tenant_state_id]
        if truck_id:
            pings = [p for p in pings if p.truck_id == truck_id]
        return pings

    # -- levy --------------------------------------------------------------------

    def get_levy_policy(self, tenant_state_id: str) -> LevyPolicy:
        policy = self.levy_policies.get(tenant_state_id)
        if policy is None:
            policy = LevyPolicy(tenant_state_id=tenant_state_id,
                                **DEFAULT_LEVY_POLICY)
        return policy

    def set_levy_policy(self, tenant_state_id: str, flat_fee_kobo: int,
                        ad_valorem_bps: int) -> LevyPolicy:
        policy = LevyPolicy(tenant_state_id=tenant_state_id,
                            flat_fee_kobo=flat_fee_kobo,
                            ad_valorem_bps=ad_valorem_bps)
        with self._lock:
            self.levy_policies[tenant_state_id] = policy
        return policy

    def quote_levy(self, tenant_state_id: str,
                   declared_value_kobo: int) -> dict:
        """Pure integer-kobo levy math: flat + value * bps / 10_000 (floor)."""
        policy = self.get_levy_policy(tenant_state_id)
        ad_valorem = (declared_value_kobo * policy.ad_valorem_bps) // 10_000
        return {
            "tenant_state_id": tenant_state_id,
            "declared_value_kobo": declared_value_kobo,
            "flat_fee_kobo": policy.flat_fee_kobo,
            "ad_valorem_bps": policy.ad_valorem_bps,
            "ad_valorem_kobo": ad_valorem,
            "total_kobo": policy.flat_fee_kobo + ad_valorem,
        }

    def assess_levy(self, tenant_state_id: str,
                    consignment_id: str) -> LevyAssessment:
        consignment = self.get_consignment(tenant_state_id, consignment_id)
        quote = self.quote_levy(tenant_state_id,
                                consignment.declared_value_kobo)
        assessment = LevyAssessment(
            assessment_id=f"lev-{uuid4().hex[:10]}",
            tenant_state_id=tenant_state_id,
            consignment_id=consignment_id,
            flat_fee_kobo=quote["flat_fee_kobo"],
            ad_valorem_bps=quote["ad_valorem_bps"],
            ad_valorem_kobo=quote["ad_valorem_kobo"],
            total_kobo=quote["total_kobo"],
            ledger_entries=[
                LedgerEntry(
                    entry_id=f"led-{uuid4().hex[:10]}",
                    tenant_state_id=tenant_state_id,
                    consignment_id=consignment_id,
                    debit_account=f"trader:{consignment.trader_ref}",
                    credit_account=f"state:{tenant_state_id}:transit_levy_revenue",
                    amount_kobo=quote["total_kobo"],
                    memo="transit levy assessment (flat + ad-valorem)",
                )
            ],
            assessed_at=self._clock(),
        )
        with self._lock:
            self.assessments[assessment.assessment_id] = assessment
        self._bus.publish(TOPIC_LEVY_ASSESSED, assessment)
        return assessment

    # -- clearance + hash-chained audit -------------------------------------------

    def clear_consignment(self, tenant_state_id: str, consignment_id: str,
                          officer_ref: str) -> ClearanceAuditRecord:
        consignment = self.transition(tenant_state_id, consignment_id, "cleared")
        payload = {
            "event_id": f"clr-{uuid4().hex[:10]}",
            "tenant_state_id": tenant_state_id,
            "consignment_id": consignment.consignment_id,
            "decision": "cleared",
            "officer_ref": officer_ref,
            "decided_at": self._clock(),
        }
        with self._lock:
            prev_hash = (self.clearance_chain[-1].event_hash
                         if self.clearance_chain else GENESIS_PREV_HASH)
            record = ClearanceAuditRecord(
                **payload,
                prev_hash=prev_hash,
                event_hash=event_payload_hash(payload, prev_hash),
            )
            self.clearance_chain.append(record)
        return record

    def verify_clearance_chain(self) -> List[str]:
        """Empty list = chain intact; errors indicate tampering."""
        return verify_event_chain(
            [r.model_dump(mode="json") for r in self.clearance_chain]
        )
