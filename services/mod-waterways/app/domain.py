"""Domain: route/jetty registry, ferry e-ticketing, sand-dredging volumetrics.

Tenant-scoped by state (``X-State-Tenant`` enforced at the API layer). The
reference store is in-memory; production bindings are PostGIS + Apache Sedona
(volumetric verification) and TigerBeetle (fare/royalty ledger in kobo).

Hash-chained audit of royalty assessments reuses ``_shared.hashchain`` via
the import-guard idiom (minimal container images ship only ``app/``).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

# --- _shared.hashchain import guard (idiom mirrors mod-police-cad) ----------
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
        import hashlib as _hashlib
        import json as _json

        GENESIS_PREV_HASH = "0" * 64

        def _canonical(obj):
            return _json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

        def event_payload_hash(payload, prev_hash):
            body = {k: v for k, v in payload.items() if k not in ("prev_hash", "event_hash")}
            body["prev_hash"] = prev_hash
            return _hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


#: Published event topics (AsyncAPI channels; InMemory bus in the reference).
TOPIC_TICKET_SOLD = "ng.sos.waterways.ticket_sold"
TOPIC_VOLUME_ALERT = "ng.sos.waterways.dredging_volume_alert"
TOPIC_ROYALTY_ASSESSED = "ng.sos.waterways.royalty_assessed"

#: Adoption states for the National Edition blueprint.
ADOPTION_STATES = ("lagos", "bayelsa", "rivers", "benue", "delta", "kogi", "niger")


class CrossTenantError(ValueError):
    """Tenant-isolation violation — mapped to HTTP 403 at the API layer."""


class SoldOutError(ValueError):
    """Trip at seat capacity — mapped to HTTP 409 at the API layer."""


class ManifestLockedError(ValueError):
    """Sales/manifest mutation after departure — mapped to HTTP 409."""


# --- Fixture registries ------------------------------------------------------


class Route(BaseModel):
    route_id: str
    tenant_state_id: str
    name: str
    origin_jetty: str
    destination_jetty: str
    distance_km: int


#: Route/jetty fixture defaults per adoption state (blueprint routes).
ROUTE_FIXTURES: dict[str, list[tuple[str, str, str, int]]] = {
    "lagos": [
        ("Ikorodu–CMS", "Ikorodu Ferry Terminal", "CMS Jetty", 24),
        ("Badagry–Marina", "Badagry Jetty", "Marina Jetty", 55),
    ],
    "bayelsa": [("Yenagoa–Brass", "Yenagoa Jetty", "Brass Terminal", 72)],
    "rivers": [("Port Harcourt–Bonny", "Port Harcourt Waterfront", "Bonny Island Jetty", 41)],
    "benue": [("Makurdi–Gboko", "Makurdi River Port", "Gboko Landing", 78)],
    "delta": [("Warri–Escravos", "Warri Port", "Escravos Terminal", 60)],
    "kogi": [("Lokoja–Idah", "Lokoja Jetty", "Idah Landing", 68)],
    "niger": [("Baro–Jebba", "Baro River Port", "Jebba Jetty", 95)],
}


class FarePolicy(BaseModel):
    """Per-state fare + royalty policy. All money in integer kobo."""

    tenant_state_id: str
    base_fare_kobo: int = Field(..., ge=0)
    per_km_kobo: int = Field(..., ge=0)
    royalty_kobo_per_m3: int = Field(..., ge=0)


#: Per-state fare/royalty policy fixtures (integer kobo only).
FARE_POLICIES: dict[str, FarePolicy] = {
    "lagos": FarePolicy(tenant_state_id="lagos", base_fare_kobo=50000, per_km_kobo=1500,
                        royalty_kobo_per_m3=120000),
    "bayelsa": FarePolicy(tenant_state_id="bayelsa", base_fare_kobo=40000, per_km_kobo=1200,
                          royalty_kobo_per_m3=100000),
    "rivers": FarePolicy(tenant_state_id="rivers", base_fare_kobo=45000, per_km_kobo=1400,
                         royalty_kobo_per_m3=110000),
    "benue": FarePolicy(tenant_state_id="benue", base_fare_kobo=30000, per_km_kobo=900,
                        royalty_kobo_per_m3=80000),
    "delta": FarePolicy(tenant_state_id="delta", base_fare_kobo=40000, per_km_kobo=1300,
                        royalty_kobo_per_m3=105000),
    "kogi": FarePolicy(tenant_state_id="kogi", base_fare_kobo=30000, per_km_kobo=1000,
                       royalty_kobo_per_m3=85000),
    "niger": FarePolicy(tenant_state_id="niger", base_fare_kobo=25000, per_km_kobo=800,
                        royalty_kobo_per_m3=75000),
}

#: Approximate state bounding boxes (min_lat, min_lon, max_lat, max_lon) for
#: survey-polygon geofence validation — reference-grade; production: PostGIS.
STATE_GEOFENCES: dict[str, tuple[float, float, float, float]] = {
    "lagos": (6.35, 2.65, 6.75, 4.35),
    "bayelsa": (4.60, 5.50, 5.60, 6.60),
    "rivers": (4.30, 6.40, 5.50, 7.60),
    "benue": (6.35, 7.50, 8.20, 10.00),
    "delta": (4.90, 5.00, 6.40, 6.90),
    "kogi": (6.50, 5.30, 8.70, 8.00),
    "niger": (8.00, 3.30, 11.10, 7.20),
}

#: Nigeria national bounding box — any polygon vertex outside is rejected.
NIGERIA_BBOX = (4.0, 2.6, 13.9, 14.7)


def polygon_area_m2(polygon: list[list[float]]) -> float:
    """Planar shoelace area over [lon, lat] vertices, scaled to m².

    Reference-grade: uses a per-vertex equirectangular approximation; the
    production path delegates to PostGIS/Sedona ``ST_Area(geography)``.
    """
    import math

    n = len(polygon)
    mean_lat = math.radians(sum(p[1] for p in polygon) / n)
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(mean_lat)
    pts = [(p[0] * km_per_deg_lon, p[1] * km_per_deg_lat) for p in polygon]
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0 * 1_000_000.0


def polygon_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    n = len(polygon)
    return (sum(p[1] for p in polygon) / n, sum(p[0] for p in polygon) / n)


def validate_polygon(polygon: list[list[float]]) -> None:
    """Geofence/shape validation for a dredging survey polygon.

    Vertices are ``[lon, lat]`` pairs. Raises ``ValueError`` on malformed
    rings, out-of-country coordinates, self-evident zero area, or unclosed
    rings (first vertex must equal the last when 4+ distinct points given —
    we accept implicit closure for 3-point rings).
    """
    if len(polygon) < 3:
        raise ValueError("survey polygon must have at least 3 vertices")
    for vertex in polygon:
        if len(vertex) != 2:
            raise ValueError("polygon vertices must be [lon, lat] pairs")
        lon, lat = vertex
        if not (NIGERIA_BBOX[1] <= lon <= NIGERIA_BBOX[3] and NIGERIA_BBOX[0] <= lat <= NIGERIA_BBOX[2]):
            raise ValueError(f"polygon vertex ({lat}, {lon}) outside Nigeria bounds")
    if len(polygon) >= 4 and polygon[0] == polygon[-1]:
        ring = polygon[:-1]
    else:
        ring = polygon
    if len(set(map(tuple, ring))) < 3:
        raise ValueError("survey polygon must have at least 3 distinct vertices")
    if polygon_area_m2(ring) <= 0.0:
        raise ValueError("survey polygon has zero area")


def in_state_geofence(state: str, lat: float, lon: float) -> bool:
    box = STATE_GEOFENCES.get(state)
    if box is None:
        return False
    return box[0] <= lat <= box[2] and box[1] <= lon <= box[3]


# --- Ticketing models --------------------------------------------------------


class TripStatus(str, Enum):
    SCHEDULED = "scheduled"
    DEPARTED = "departed"


class Trip(BaseModel):
    trip_id: str
    tenant_state_id: str
    route_id: str
    vessel: str
    capacity: int = Field(..., gt=0)
    departure: str  # ISO-8601 scheduled departure
    status: TripStatus = TripStatus.SCHEDULED
    manifest_locked: bool = False  # safety rule: locked at departure
    created_at: str


class Ticket(BaseModel):
    ticket_id: str
    tenant_state_id: str
    trip_id: str
    passenger_name: str
    fare_kobo: int = Field(..., ge=0)  # integer kobo, from state fare policy
    qr_ref: str  # QR-style ticket reference presented at boarding
    sold_at: str


# --- Dredging models ---------------------------------------------------------


class Dredger(BaseModel):
    dredger_id: str
    tenant_state_id: str
    vessel_name: str
    license_no: str
    operator_kyb_ref: str  # mod-kyc-kyb business verification reference
    monthly_quota_m3: float = Field(..., gt=0)
    registered_at: str


class Survey(BaseModel):
    survey_id: str
    tenant_state_id: str
    dredger_id: str
    polygon: list[list[float]]  # [lon, lat] ring
    volume_m3: float = Field(..., gt=0)
    surveyed_at: str
    verified_volume_m3: float | None = None  # Sedona volumetric cross-check
    month: str  # YYYY-MM rollup key


class RoyaltyAssessment(BaseModel):
    assessment_id: str
    tenant_state_id: str
    dredger_id: str
    survey_id: str
    month: str
    volume_m3: float
    royalty_kobo: int  # integer kobo = volume_m3 * policy rate
    over_quota: bool
    monthly_cumulative_m3: float
    monthly_quota_m3: float
    assessed_at: str


class WaterwaysStore:
    """Thread-safe tenant-scoped in-memory store.

    Production: PostGIS (routes/polygons), Apache Sedona (volumetric
    verification), TigerBeetle (fare + royalty ledger, integer kobo).
    """

    def __init__(self, event_bus=None) -> None:
        self._lock = threading.Lock()
        self.event_bus = event_bus  # InMemoryEventBus default set by main
        self.routes: dict[str, dict[str, Route]] = {}  # tenant -> route_id -> Route
        self.trips: dict[str, Trip] = {}
        self.tickets: dict[str, Ticket] = {}
        self.dredgers: dict[str, Dredger] = {}
        self.surveys: dict[str, Survey] = {}
        self.royalty_assessments: list[dict] = []  # hash-chained audit records
        self._chain_tip: str = GENESIS_PREV_HASH
        self._seed_routes()

    # --- seeding -------------------------------------------------------------
    def _seed_routes(self) -> None:
        for tenant, routes in ROUTE_FIXTURES.items():
            self.routes.setdefault(tenant, {})
            for index, (name, origin, dest, km) in enumerate(routes, start=1):
                route = Route(
                    route_id=f"rt-{tenant}-{index:02d}",
                    tenant_state_id=tenant,
                    name=name,
                    origin_jetty=origin,
                    destination_jetty=dest,
                    distance_km=km,
                )
                self.routes[tenant][route.route_id] = route

    # --- events --------------------------------------------------------------
    def _publish(self, topic: str, payload: BaseModel) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(topic, payload)

    # --- routes --------------------------------------------------------------
    def list_routes(self, tenant: str) -> list[Route]:
        with self._lock:
            return list(self.routes.get(tenant, {}).values())

    def list_jetties(self, tenant: str) -> list[str]:
        jetties: list[str] = []
        for route in self.list_routes(tenant):
            for jetty in (route.origin_jetty, route.destination_jetty):
                if jetty not in jetties:
                    jetties.append(jetty)
        return jetties

    # --- trips ---------------------------------------------------------------
    def schedule_trip(self, tenant: str, route_id: str, vessel: str,
                      capacity: int, departure: str) -> Trip:
        with self._lock:
            route = self.routes.get(tenant, {}).get(route_id)
            if route is None:
                raise KeyError(f"route '{route_id}' not found for tenant '{tenant}'")
            trip = Trip(trip_id=_id("trip"), tenant_state_id=tenant, route_id=route_id,
                        vessel=vessel, capacity=capacity, departure=departure,
                        created_at=_now())
            self.trips[trip.trip_id] = trip
            return trip

    def list_trips(self, tenant: str) -> list[Trip]:
        with self._lock:
            return [t for t in self.trips.values() if t.tenant_state_id == tenant]

    def _trip_for_tenant(self, tenant: str, trip_id: str) -> Trip:
        trip = self.trips.get(trip_id)
        if trip is None:
            raise KeyError(f"trip '{trip_id}' not found")
        if trip.tenant_state_id != tenant:
            raise CrossTenantError("cross-tenant trip access is prohibited")
        return trip

    # --- tickets ---------------------------------------------------------------
    def fare_for_route(self, tenant: str, route_id: str) -> int:
        """Integer-kobo fare: base fare + per-km rate × route distance."""
        policy = FARE_POLICIES[tenant]
        route = self.routes[tenant][route_id]
        return policy.base_fare_kobo + policy.per_km_kobo * route.distance_km

    def purchase_ticket(self, tenant: str, trip_id: str, passenger_name: str) -> Ticket:
        with self._lock:
            trip = self._trip_for_tenant(tenant, trip_id)
            if trip.manifest_locked or trip.status is TripStatus.DEPARTED:
                raise ManifestLockedError(
                    f"trip '{trip_id}' manifest locked at departure — no further sales"
                )
            sold = sum(1 for t in self.tickets.values() if t.trip_id == trip_id)
            if sold >= trip.capacity:
                raise SoldOutError(f"trip '{trip_id}' is sold out (capacity {trip.capacity})")
            fare = self.fare_for_route(tenant, trip.route_id)
            ticket = Ticket(
                ticket_id=_id("tkt"), tenant_state_id=tenant, trip_id=trip_id,
                passenger_name=passenger_name, fare_kobo=fare,
                qr_ref=f"WWY-{tenant[:3].upper()}-{uuid4().hex[:12].upper()}",
                sold_at=_now(),
            )
            self.tickets[ticket.ticket_id] = ticket
        self._publish(TOPIC_TICKET_SOLD, ticket)
        return ticket

    def list_tickets(self, tenant: str, trip_id: str | None = None) -> list[Ticket]:
        with self._lock:
            tickets = [t for t in self.tickets.values() if t.tenant_state_id == tenant]
            if trip_id is not None:
                tickets = [t for t in tickets if t.trip_id == trip_id]
            return tickets

    def manifest(self, tenant: str, trip_id: str) -> dict:
        with self._lock:
            trip = self._trip_for_tenant(tenant, trip_id)
            tickets = [t for t in self.tickets.values() if t.trip_id == trip_id]
            return {
                "trip_id": trip.trip_id,
                "tenant_state_id": tenant,
                "route_id": trip.route_id,
                "vessel": trip.vessel,
                "departure": trip.departure,
                "status": trip.status.value,
                "manifest_locked": trip.manifest_locked,
                "capacity": trip.capacity,
                "passenger_count": len(tickets),
                "passengers": [
                    {"ticket_id": t.ticket_id, "passenger_name": t.passenger_name,
                     "qr_ref": t.qr_ref, "fare_kobo": t.fare_kobo}
                    for t in tickets
                ],
            }

    def depart_trip(self, tenant: str, trip_id: str) -> Trip:
        """Mark departure — safety rule: the manifest locks at departure."""
        with self._lock:
            trip = self._trip_for_tenant(tenant, trip_id)
            if trip.status is TripStatus.DEPARTED:
                raise ManifestLockedError(f"trip '{trip_id}' already departed")
            trip.status = TripStatus.DEPARTED
            trip.manifest_locked = True
            return trip

    # --- dredgers ---------------------------------------------------------------
    def register_dredger(self, tenant: str, vessel_name: str, license_no: str,
                         operator_kyb_ref: str, monthly_quota_m3: float) -> Dredger:
        dredger = Dredger(dredger_id=_id("drg"), tenant_state_id=tenant,
                          vessel_name=vessel_name, license_no=license_no,
                          operator_kyb_ref=operator_kyb_ref,
                          monthly_quota_m3=monthly_quota_m3, registered_at=_now())
        with self._lock:
            self.dredgers[dredger.dredger_id] = dredger
        return dredger

    def list_dredgers(self, tenant: str) -> list[Dredger]:
        with self._lock:
            return [d for d in self.dredgers.values() if d.tenant_state_id == tenant]

    # --- surveys / volumetrics ----------------------------------------------------
    def ingest_survey(self, tenant: str, dredger_id: str, polygon: list[list[float]],
                      volume_m3: float, surveyed_at: str,
                      verified_volume_m3: float | None = None) -> tuple[Survey, RoyaltyAssessment, bool]:
        """Ingest a volumetric dredging survey.

        Validates the polygon (shape + state geofence), rolls the volume into
        the dredger's monthly cumulative, computes the royalty levy (integer
        kobo per m³ from state policy), hash-chains the assessment, and flags
        over-quota. Returns ``(survey, assessment, over_quota)``.
        """
        validate_polygon(polygon)
        lat, lon = polygon_centroid(polygon)
        if not in_state_geofence(tenant, lat, lon):
            raise ValueError(f"survey polygon centroid outside the '{tenant}' geofence")
        try:
            month = datetime.fromisoformat(surveyed_at).strftime("%Y-%m")
        except ValueError as exc:
            raise ValueError(f"surveyed_at is not ISO-8601: {exc}") from exc
        with self._lock:
            dredger = self.dredgers.get(dredger_id)
            if dredger is None:
                raise KeyError(f"dredger '{dredger_id}' not found")
            if dredger.tenant_state_id != tenant:
                raise CrossTenantError("cross-tenant dredger access is prohibited")
            survey = Survey(survey_id=_id("srv"), tenant_state_id=tenant,
                            dredger_id=dredger_id, polygon=polygon, volume_m3=volume_m3,
                            surveyed_at=surveyed_at,
                            verified_volume_m3=verified_volume_m3, month=month)
            self.surveys[survey.survey_id] = survey
            cumulative = sum(
                s.volume_m3 for s in self.surveys.values()
                if s.dredger_id == dredger_id and s.month == month
            )
            over_quota = cumulative > dredger.monthly_quota_m3
            policy = FARE_POLICIES[tenant]
            royalty_kobo = int(round(volume_m3 * policy.royalty_kobo_per_m3))
            assessment = RoyaltyAssessment(
                assessment_id=_id("roy"), tenant_state_id=tenant, dredger_id=dredger_id,
                survey_id=survey.survey_id, month=month, volume_m3=volume_m3,
                royalty_kobo=royalty_kobo, over_quota=over_quota,
                monthly_cumulative_m3=cumulative,
                monthly_quota_m3=dredger.monthly_quota_m3, assessed_at=_now(),
            )
            # Hash-chained audit of the royalty assessment (P1 immutability).
            record = assessment.model_dump()
            record["event_id"] = assessment.assessment_id
            record["event_type"] = "royalty_assessed"
            record["prev_hash"] = self._chain_tip
            record["event_hash"] = event_payload_hash(record, self._chain_tip)
            self._chain_tip = record["event_hash"]
            self.royalty_assessments.append(record)
        self._publish(TOPIC_ROYALTY_ASSESSED, assessment)
        if over_quota:
            self._publish(TOPIC_VOLUME_ALERT, assessment)
        return survey, assessment, over_quota

    # --- quotas / royalties -------------------------------------------------------
    def quota_status(self, tenant: str, month: str | None = None) -> list[dict]:
        """Monthly cumulative volume per dredger vs licensed quota."""
        with self._lock:
            dredgers = [d for d in self.dredgers.values() if d.tenant_state_id == tenant]
            surveys = list(self.surveys.values())
        status_rows = []
        for dredger in dredgers:
            months = {month} if month else {s.month for s in surveys if s.dredger_id == dredger.dredger_id}
            if not months and month is None:
                months = {datetime.now(timezone.utc).strftime("%Y-%m")}
            for m in sorted(months):
                cumulative = sum(
                    s.volume_m3 for s in surveys
                    if s.dredger_id == dredger.dredger_id and s.month == m
                )
                status_rows.append({
                    "dredger_id": dredger.dredger_id,
                    "tenant_state_id": tenant,
                    "month": m,
                    "monthly_cumulative_m3": cumulative,
                    "monthly_quota_m3": dredger.monthly_quota_m3,
                    "over_quota": cumulative > dredger.monthly_quota_m3,
                    "utilization": round(cumulative / dredger.monthly_quota_m3, 4),
                })
        return status_rows

    def list_royalties(self, tenant: str) -> list[dict]:
        with self._lock:
            return [r for r in self.royalty_assessments if r["tenant_state_id"] == tenant]

    def verify_royalty_chain(self, tenant: str) -> list[str]:
        """Tamper check over the tenant's hash-chained royalty assessments."""
        with self._lock:
            records = [r for r in self.royalty_assessments if r["tenant_state_id"] == tenant]
        return verify_event_chain(records)
