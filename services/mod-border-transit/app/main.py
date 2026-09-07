"""FastAPI surface for mod-border-transit (National Edition blueprint).

Cross-Border Cargo RFID Tracking & Transit Telematics. Every endpoint is
tenant-scoped by the ``X-State-Tenant`` header (400 when missing). Fail-closed
adapters (RFID reader, telematics) are bound at boot; the shared event bus
publishes ``ng.sos.border.*`` topics.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .adapters import (
    RfidReaderAdapter,
    TelematicsAdapter,
    build_rfid_adapter,
    build_telematics_adapter,
)
from .domain import (
    BorderTransitStore,
    Consignment,
    CrossTenantError,
    Crossing,
    InvalidTransitionError,
    LevyAssessment,
    LevyPolicy,
    ScanEvent,
    TamperAlert,
    TelematicsPing,
)

# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None

# --- Shared event bus (services/_shared/eventbus); InMemory default --------
try:
    from _shared.eventbus import event_bus_from_env as _event_bus_from_env
except ImportError:
    import sys as _sys2
    from pathlib import Path as _Path2

    _services_root2 = _Path2(__file__).resolve().parents[2]
    if str(_services_root2) not in _sys2.path:
        _sys2.path.insert(0, str(_services_root2))
    try:
        from _shared.eventbus import event_bus_from_env as _event_bus_from_env
    except ImportError:
        _event_bus_from_env = None


def tenant_from_header(x_state_tenant: str | None = Header(default=None)) -> str:
    """All state is scoped by the ``X-State-Tenant`` header (multi-tenancy)."""
    if not x_state_tenant:
        raise HTTPException(status_code=400,
                            detail="X-State-Tenant header is required")
    return x_state_tenant.lower()


class CrossingUpsert(BaseModel):
    name: str
    neighbor_country: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class ConsignmentDeclare(BaseModel):
    trader_ref: str
    rfid_tag_id: str
    goods_description: str
    hs_code: str
    declared_value_kobo: int = Field(..., ge=0)
    origin_crossing_id: str
    destination_crossing_id: str


class ScanIngest(BaseModel):
    consignment_id: str
    checkpoint_id: str
    direction: str = Field(..., pattern="^(entry|exit)$")
    scanned_at: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    seal_intact: bool = True
    rfid_tag_id: Optional[str] = None


class PingIngest(BaseModel):
    truck_id: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    speed_kph: float = Field(..., ge=0)
    pinged_at: str
    consignment_id: Optional[str] = None


class LevyQuoteRequest(BaseModel):
    declared_value_kobo: int = Field(..., ge=0)


class LevyPolicyUpdate(BaseModel):
    flat_fee_kobo: int = Field(..., ge=0)
    ad_valorem_bps: int = Field(..., ge=0, le=10_000)


class ClearanceRequest(BaseModel):
    officer_ref: str


def get_store(request: Request) -> BorderTransitStore:
    return request.app.state.store


def get_rfid(request: Request) -> RfidReaderAdapter:
    return request.app.state.rfid


def get_telematics(request: Request) -> TelematicsAdapter:
    return request.app.state.telematics


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=exc.args[0])


def create_app(store: BorderTransitStore | None = None) -> FastAPI:
    app = FastAPI(
        title="SOS mod-border-transit — Cross-Border Cargo RFID Tracking & "
              "Transit Telematics",
        version="0.1.0",
        description="Border crossing registry, RFID-tracked transit "
                    "consignments, corridor geofencing, tamper alerts, and "
                    "transit levy assessment for the six adoption states.",
    )
    if store is None:
        bus = _event_bus_from_env() if _event_bus_from_env else None
        store = BorderTransitStore(bus=bus)
    app.state.store = store
    # Fail-closed adapter bindings: default fixture profile is deterministic;
    # SOS_BORDER_PROFILE=production hard-fails here at boot without config.
    app.state.rfid = build_rfid_adapter()
    app.state.telematics = build_telematics_adapter()

    # --- crossings CRUD ------------------------------------------------------
    @app.get("/border/v1/crossings", response_model=list[Crossing],
             tags=["crossings"])
    def list_crossings(tenant: str = Depends(tenant_from_header),
                       store: BorderTransitStore = Depends(get_store)):
        return store.list_crossings(tenant)

    @app.post("/border/v1/crossings/{crossing_id}",
              status_code=status.HTTP_201_CREATED, response_model=Crossing,
              tags=["crossings"])
    def create_crossing(crossing_id: str, req: CrossingUpsert,
                        tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        try:
            return store.create_crossing(tenant, crossing_id, req.name,
                                         req.neighbor_country, req.latitude,
                                         req.longitude)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/border/v1/crossings/{crossing_id}", response_model=Crossing,
             tags=["crossings"])
    def get_crossing(crossing_id: str, tenant: str = Depends(tenant_from_header),
                     store: BorderTransitStore = Depends(get_store)):
        try:
            return store.get_crossing(tenant, crossing_id)
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.patch("/border/v1/crossings/{crossing_id}", response_model=Crossing,
               tags=["crossings"])
    def update_crossing(crossing_id: str, req: CrossingUpsert,
                        tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        try:
            return store.update_crossing(tenant, crossing_id, **req.model_dump())
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.delete("/border/v1/crossings/{crossing_id}",
                status_code=status.HTTP_204_NO_CONTENT, tags=["crossings"])
    def delete_crossing(crossing_id: str,
                        tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        try:
            store.delete_crossing(tenant, crossing_id)
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    # --- consignments ----------------------------------------------------------
    @app.post("/border/v1/consignments", status_code=status.HTTP_201_CREATED,
              response_model=Consignment, tags=["consignments"])
    def declare_consignment(req: ConsignmentDeclare,
                            tenant: str = Depends(tenant_from_header),
                            store: BorderTransitStore = Depends(get_store)):
        try:
            return store.declare_consignment(
                tenant, req.trader_ref, req.rfid_tag_id, req.goods_description,
                req.hs_code, req.declared_value_kobo, req.origin_crossing_id,
                req.destination_crossing_id,
            )
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.get("/border/v1/consignments", response_model=list[Consignment],
             tags=["consignments"])
    def list_consignments(state: Optional[str] = None,
                          tenant: str = Depends(tenant_from_header),
                          store: BorderTransitStore = Depends(get_store)):
        return store.list_consignments(tenant, state)

    @app.get("/border/v1/consignments/{consignment_id}",
             response_model=Consignment, tags=["consignments"])
    def get_consignment(consignment_id: str,
                        tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        try:
            return store.get_consignment(tenant, consignment_id)
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    def _transition(consignment_id: str, target: str, tenant: str,
                    store: BorderTransitStore) -> Consignment:
        try:
            return store.transition(tenant, consignment_id, target)
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/border/v1/consignments/{consignment_id}/seal",
              response_model=Consignment, tags=["consignments"])
    def seal_consignment(consignment_id: str,
                         tenant: str = Depends(tenant_from_header),
                         store: BorderTransitStore = Depends(get_store)):
        return _transition(consignment_id, "sealed", tenant, store)

    @app.post("/border/v1/consignments/{consignment_id}/arrive",
              response_model=Consignment, tags=["consignments"])
    def arrive_consignment(consignment_id: str,
                           tenant: str = Depends(tenant_from_header),
                           store: BorderTransitStore = Depends(get_store)):
        return _transition(consignment_id, "arrived", tenant, store)

    @app.post("/border/v1/consignments/{consignment_id}/clear",
              tags=["consignments"])
    def clear_consignment(consignment_id: str, req: ClearanceRequest,
                          tenant: str = Depends(tenant_from_header),
                          store: BorderTransitStore = Depends(get_store)):
        """Clear the consignment and append a hash-chained audit record."""
        try:
            return store.clear_consignment(tenant, consignment_id,
                                           req.officer_ref)
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/border/v1/clearance-audit", tags=["consignments"])
    def clearance_audit(tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        """Hash-chained clearance decisions for this tenant plus chain
        integrity status (errors non-empty ⇒ tampering detected)."""
        records = [r for r in store.clearance_chain
                   if r.tenant_state_id == tenant]
        return {"records": records,
                "chain_errors": store.verify_clearance_chain()}

    # --- RFID scans ---------------------------------------------------------------
    @app.post("/border/v1/scans", status_code=status.HTTP_201_CREATED,
              response_model=ScanEvent, tags=["rfid"])
    def ingest_scan(req: ScanIngest,
                    tenant: str = Depends(tenant_from_header),
                    store: BorderTransitStore = Depends(get_store)):
        try:
            return store.record_scan(
                tenant, req.consignment_id, req.checkpoint_id, req.direction,
                req.scanned_at, req.latitude, req.longitude,
                seal_intact=req.seal_intact, scanned_tag_id=req.rfid_tag_id,
            )
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.get("/border/v1/scans", response_model=list[ScanEvent], tags=["rfid"])
    def list_scans(consignment_id: Optional[str] = None,
                   tenant: str = Depends(tenant_from_header),
                   store: BorderTransitStore = Depends(get_store)):
        return store.list_scans(tenant, consignment_id)

    @app.post("/border/v1/scans/pull", status_code=status.HTTP_201_CREATED,
              tags=["rfid"])
    def pull_reader_scans(tenant: str = Depends(tenant_from_header),
                          rfid: RfidReaderAdapter = Depends(get_rfid)):
        """Pull pending scans from the bound RFID reader adapter (fixture by
        default; live seam ``SOS_BORDER_RFID_URL`` in production)."""
        return {"scans": rfid.fetch_scans(tenant)}

    # --- telematics ------------------------------------------------------------------
    @app.post("/border/v1/telematics/pings",
              status_code=status.HTTP_201_CREATED, response_model=TelematicsPing,
              tags=["telematics"])
    def ingest_ping(req: PingIngest,
                    tenant: str = Depends(tenant_from_header),
                    store: BorderTransitStore = Depends(get_store)):
        """GPS ping ingest with corridor geofence check; out-of-corridor pings
        raise a ``geofence_deviation`` tamper alert."""
        return store.ingest_ping(tenant, req.truck_id, req.latitude,
                                 req.longitude, req.speed_kph, req.pinged_at,
                                 req.consignment_id)

    @app.get("/border/v1/telematics/pings",
             response_model=list[TelematicsPing], tags=["telematics"])
    def list_pings(truck_id: Optional[str] = None,
                   tenant: str = Depends(tenant_from_header),
                   store: BorderTransitStore = Depends(get_store)):
        return store.list_pings(tenant, truck_id)

    @app.post("/border/v1/telematics/pull", status_code=status.HTTP_201_CREATED,
              tags=["telematics"])
    def pull_telematics(tenant: str = Depends(tenant_from_header),
                        adapter: TelematicsAdapter = Depends(get_telematics)):
        """Pull pending GPS pings from the bound telematics adapter."""
        return {"pings": adapter.fetch_pings(tenant)}

    # --- tamper alerts -----------------------------------------------------------------
    @app.get("/border/v1/tamper-alerts", response_model=list[TamperAlert],
             tags=["alerts"])
    def list_tamper_alerts(tenant: str = Depends(tenant_from_header),
                           store: BorderTransitStore = Depends(get_store)):
        return store.list_tamper_alerts(tenant)

    # --- transit levy ---------------------------------------------------------------------
    @app.post("/border/v1/levy/quote", tags=["levy"])
    def levy_quote(req: LevyQuoteRequest,
                   tenant: str = Depends(tenant_from_header),
                   store: BorderTransitStore = Depends(get_store)):
        return store.quote_levy(tenant, req.declared_value_kobo)

    @app.post("/border/v1/levy/assess/{consignment_id}",
              status_code=status.HTTP_201_CREATED,
              response_model=LevyAssessment, tags=["levy"])
    def levy_assess(consignment_id: str,
                    tenant: str = Depends(tenant_from_header),
                    store: BorderTransitStore = Depends(get_store)):
        try:
            return store.assess_levy(tenant, consignment_id)
        except KeyError as exc:
            raise _not_found(exc)
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.get("/border/v1/levy/policy", response_model=LevyPolicy, tags=["levy"])
    def get_levy_policy(tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        return store.get_levy_policy(tenant)

    @app.put("/border/v1/levy/policy", response_model=LevyPolicy, tags=["levy"])
    def put_levy_policy(req: LevyPolicyUpdate,
                        tenant: str = Depends(tenant_from_header),
                        store: BorderTransitStore = Depends(get_store)):
        return store.set_levy_policy(tenant, req.flat_fee_kobo,
                                     req.ad_valorem_bps)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "mod-border-transit"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-border-transit")

    return app


app = create_app()
