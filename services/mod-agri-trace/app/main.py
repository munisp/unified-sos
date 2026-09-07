"""FastAPI surface for mod-agri-trace — Commodity Aggregation, Agro-Hub
Warehouse Receipts & Crop Traceability.

All endpoints are tenant-scoped by the ``X-State-Tenant`` header (400 when
missing). Warehouse receipts are hash-chained and lifecycle transitions are
enforced (invalid transitions -> HTTP 409). Pledge/redemption emit settlement
intent events to the shared event bus (Mojaloop hook seam).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .adapters import (
    AdapterCallError,
    CommodityExchangeAdapter,
    TelemetryReading,
    WarehouseIoTAdapter,
    build_exchange_adapter,
    build_iot_adapter,
)
from .adapters import PriceQuote
from .domain import (
    EVENT_LOT_TRACED_HOP,
    EVENT_RECEIPT_ISSUED,
    EVENT_RECEIPT_PLEDGED,
    EVENT_RECEIPT_REDEEMED,
    AgriStore,
    Farmer,
    InvalidTransitionError,
    Lot,
    LotTracedHopEvent,
    QualityGrade,
    TraceHop,
    TraceStage,
    Warehouse,
    WarehouseReceipt,
    catalog_for,
)

# --- shared event bus (services/_shared/eventbus) -----------------------------
try:
    from _shared.eventbus import EventBus, InMemoryEventBus, event_bus_from_env
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.eventbus import EventBus, InMemoryEventBus, event_bus_from_env
    except ImportError:  # minimal container images ship only the app package
        EventBus = None  # type: ignore[assignment]
        InMemoryEventBus = None  # type: ignore[assignment]
        event_bus_from_env = None  # type: ignore[assignment]

# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys2
    from pathlib import Path as _Path2

    _services_root2 = _Path2(__file__).resolve().parents[2]
    if str(_services_root2) not in _sys2.path:
        _sys2.path.insert(0, str(_services_root2))
    try:
        from _shared.observability import instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        _instrument_fastapi = None


def tenant_from_header(x_state_tenant: str | None = Header(default=None)) -> str:
    """All state is scoped by the ``X-State-Tenant`` header (multi-tenancy)."""
    if not x_state_tenant:
        raise HTTPException(status_code=400, detail="X-State-Tenant header is required")
    return x_state_tenant.lower()


def get_store(request: Request) -> AgriStore:
    return request.app.state.store


def get_exchange(request: Request) -> CommodityExchangeAdapter:
    return request.app.state.exchange


def get_iot(request: Request) -> WarehouseIoTAdapter:
    return request.app.state.iot


def _bus_from_env_safe():
    if event_bus_from_env is None:
        return None
    try:
        return event_bus_from_env()
    except Exception:
        # fail-soft for the reference build: fall back to in-memory and keep
        # serving; production wiring sets EVENT_BUS explicitly.
        return InMemoryEventBus()


class FarmerRegistration(BaseModel):
    name: str
    kyc_ref: str = Field(..., description="KYC reference from mod-kyc-kyb")
    lga: str = ""
    phone: str = ""


class LotIntake(BaseModel):
    farmer_id: str
    commodity: str
    weight_kg: float = Field(..., gt=0)
    grade: QualityGrade
    moisture_pct: float = Field(..., ge=0, le=100)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class WarehouseRegistration(BaseModel):
    name: str
    lga: str = ""
    capacity_kg: float = Field(..., gt=0)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class ReceiptIssueRequest(BaseModel):
    warehouse_id: str
    lot_id: str
    storage_fees_kobo: int = Field(..., ge=0)


class PledgeRequest(BaseModel):
    pledgee_ref: str = Field(..., description="Lender / collateral agent reference")


class TransferRequest(BaseModel):
    from_holder: str
    to_holder: str


class TraceHopRequest(BaseModel):
    lot_id: str
    stage: TraceStage
    actor: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    note: str = ""


class TelemetryIngestRequest(BaseModel):
    warehouse_id: str
    moisture_pct: float = Field(..., ge=0, le=100)
    temperature_c: float = Field(..., ge=-40, le=80)


def create_app(store: AgriStore | None = None,
               exchange: CommodityExchangeAdapter | None = None,
               iot: WarehouseIoTAdapter | None = None,
               bus=None) -> FastAPI:
    app = FastAPI(
        title="SOS mod-agri-trace — Commodity Aggregation, Warehouse Receipts "
              "& Crop Traceability",
        version="0.1.0",
        description="Agro-hub commodity aggregation intake, hash-chained "
                    "warehouse receipts (issued/pledged/released/redeemed, "
                    "double-entry title transfer) and lot provenance tracing.",
    )
    app.state.store = store or AgriStore()
    # Fail-closed adapter bindings: default fixture profile is deterministic;
    # SOS_AGRI_PROFILE=production hard-fails here at boot without config.
    app.state.exchange = exchange or build_exchange_adapter()
    app.state.iot = iot or build_iot_adapter()
    app.state.bus = bus if bus is not None else _bus_from_env_safe()

    def _publish(topic: str, payload: BaseModel) -> None:
        if app.state.bus is not None:
            app.state.bus.publish(topic, payload)

    # --- farmer / supplier registry ------------------------------------------
    @app.post("/agri/v1/farmers", status_code=status.HTTP_201_CREATED,
              response_model=Farmer, tags=["registry"])
    def register_farmer(req: FarmerRegistration,
                        tenant: str = Depends(tenant_from_header),
                        store: AgriStore = Depends(get_store)):
        return store.register_farmer(tenant, req.name, req.kyc_ref, req.lga, req.phone)

    @app.get("/agri/v1/farmers", response_model=list[Farmer], tags=["registry"])
    def list_farmers(tenant: str = Depends(tenant_from_header),
                     store: AgriStore = Depends(get_store)):
        return store.list_farmers(tenant)

    @app.get("/agri/v1/farmers/{farmer_id}", response_model=Farmer, tags=["registry"])
    def get_farmer(farmer_id: str, tenant: str = Depends(tenant_from_header),
                   store: AgriStore = Depends(get_store)):
        try:
            return store.get_farmer(tenant, farmer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    # --- commodity catalog ------------------------------------------------------
    @app.get("/agri/v1/commodities", tags=["catalog"])
    def commodities(tenant: str = Depends(tenant_from_header)):
        """Per-state commodity catalog (Benue yam, Osun cocoa, Taraba tea,
        Kebbi rice, Kano sorghum/maize; generic fallback otherwise)."""
        return {"tenant_state_id": tenant, "commodities": catalog_for(tenant)}

    # --- aggregation intake --------------------------------------------------------
    @app.post("/agri/v1/lots", status_code=status.HTTP_201_CREATED,
              response_model=Lot, tags=["aggregation"])
    def intake_lot(req: LotIntake, tenant: str = Depends(tenant_from_header),
                   store: AgriStore = Depends(get_store)):
        try:
            lot = store.intake_lot(tenant, req.farmer_id, req.commodity,
                                   req.weight_kg, req.grade, req.moisture_pct,
                                   req.latitude, req.longitude)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        # intake auto-records the farm hop; publish its trace event
        hop = store.tenant(tenant).trace_hops[lot.lot_id][-1]
        _publish(EVENT_LOT_TRACED_HOP, LotTracedHopEvent(
            tenant_state_id=tenant, lot_id=lot.lot_id, hop_id=hop.hop_id,
            stage=hop.stage.value, actor=hop.actor, event_hash=hop.event_hash,
            occurred_at=hop.occurred_at,
        ))
        return lot

    @app.get("/agri/v1/lots", response_model=list[Lot], tags=["aggregation"])
    def list_lots(tenant: str = Depends(tenant_from_header),
                  store: AgriStore = Depends(get_store)):
        return store.list_lots(tenant)

    # --- warehouses ----------------------------------------------------------------
    @app.post("/agri/v1/warehouses", status_code=status.HTTP_201_CREATED,
              response_model=Warehouse, tags=["warehouse"])
    def register_warehouse(req: WarehouseRegistration,
                           tenant: str = Depends(tenant_from_header),
                           store: AgriStore = Depends(get_store)):
        try:
            return store.register_warehouse(tenant, req.name, req.lga,
                                            req.capacity_kg, req.latitude,
                                            req.longitude)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/agri/v1/warehouses", response_model=list[Warehouse], tags=["warehouse"])
    def list_warehouses(tenant: str = Depends(tenant_from_header),
                        store: AgriStore = Depends(get_store)):
        return store.list_warehouses(tenant)

    # --- warehouse receipts ---------------------------------------------------------
    @app.post("/agri/v1/receipts", status_code=status.HTTP_201_CREATED,
              response_model=WarehouseReceipt, tags=["warehouse-receipts"])
    def issue_receipt(req: ReceiptIssueRequest,
                      tenant: str = Depends(tenant_from_header),
                      store: AgriStore = Depends(get_store)):
        try:
            receipt, event = store.issue_receipt(tenant, req.warehouse_id,
                                                 req.lot_id, req.storage_fees_kobo)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _publish(EVENT_RECEIPT_ISSUED, event)
        # warehouse hop auto-recorded on issue
        hop = store.tenant(tenant).trace_hops[receipt.lot_id][-1]
        _publish(EVENT_LOT_TRACED_HOP, LotTracedHopEvent(
            tenant_state_id=tenant, lot_id=receipt.lot_id, hop_id=hop.hop_id,
            stage=hop.stage.value, actor=hop.actor, event_hash=hop.event_hash,
            occurred_at=hop.occurred_at,
        ))
        return receipt

    @app.get("/agri/v1/receipts", response_model=list[WarehouseReceipt],
             tags=["warehouse-receipts"])
    def list_receipts(tenant: str = Depends(tenant_from_header),
                      store: AgriStore = Depends(get_store)):
        return store.list_receipts(tenant)

    @app.get("/agri/v1/receipts/{receipt_id}", response_model=WarehouseReceipt,
             tags=["warehouse-receipts"])
    def get_receipt(receipt_id: str, tenant: str = Depends(tenant_from_header),
                    store: AgriStore = Depends(get_store)):
        try:
            return store.get_receipt(tenant, receipt_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    def _lifecycle(receipt_id: str, tenant: str, store: AgriStore, action: str,
                   req: PledgeRequest | None = None):
        try:
            if action == "pledge":
                receipt, event = store.pledge_receipt(tenant, receipt_id,
                                                      req.pledgee_ref)  # type: ignore[union-attr]
                _publish(EVENT_RECEIPT_PLEDGED, event)
            elif action == "release":
                receipt, event = store.release_receipt(tenant, receipt_id)
                _publish(EVENT_RECEIPT_REDEEMED, event)
            else:
                receipt, event = store.redeem_receipt(tenant, receipt_id)
                _publish(EVENT_RECEIPT_REDEEMED, event)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return receipt

    @app.post("/agri/v1/receipts/{receipt_id}/pledge",
              response_model=WarehouseReceipt, tags=["warehouse-receipts"])
    def pledge_receipt(receipt_id: str, req: PledgeRequest,
                       tenant: str = Depends(tenant_from_header),
                       store: AgriStore = Depends(get_store)):
        """Pledge a WR as loan collateral; emits a settlement intent event."""
        return _lifecycle(receipt_id, tenant, store, "pledge", req)

    @app.post("/agri/v1/receipts/{receipt_id}/release",
              response_model=WarehouseReceipt, tags=["warehouse-receipts"])
    def release_receipt(receipt_id: str, tenant: str = Depends(tenant_from_header),
                        store: AgriStore = Depends(get_store)):
        return _lifecycle(receipt_id, tenant, store, "release")

    @app.post("/agri/v1/receipts/{receipt_id}/redeem",
              response_model=WarehouseReceipt, tags=["warehouse-receipts"])
    def redeem_receipt(receipt_id: str, tenant: str = Depends(tenant_from_header),
                       store: AgriStore = Depends(get_store)):
        return _lifecycle(receipt_id, tenant, store, "redeem")

    @app.post("/agri/v1/receipts/{receipt_id}/transfer",
              response_model=WarehouseReceipt, tags=["warehouse-receipts"])
    def transfer_title(receipt_id: str, req: TransferRequest,
                       tenant: str = Depends(tenant_from_header),
                       store: AgriStore = Depends(get_store)):
        """Transfer WR title between holders (double-entry ledger recorded)."""
        try:
            return store.transfer_title(tenant, receipt_id, req.from_holder,
                                        req.to_holder)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/agri/v1/receipts/{receipt_id}/ledger", tags=["warehouse-receipts"])
    def title_ledger(receipt_id: str, tenant: str = Depends(tenant_from_header),
                     store: AgriStore = Depends(get_store)):
        try:
            store.get_receipt(tenant, receipt_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        return {
            "receipt_id": receipt_id,
            "entries": store.title_ledger_for(tenant, receipt_id),
            "ledger_valid": not store.verify_title_ledger(tenant),
        }

    # --- crop traceability ----------------------------------------------------------
    @app.post("/agri/v1/trace/hops", status_code=status.HTTP_201_CREATED,
              response_model=TraceHop, tags=["traceability"])
    def add_trace_hop(req: TraceHopRequest, tenant: str = Depends(tenant_from_header),
                      store: AgriStore = Depends(get_store)):
        try:
            hop, event = store.add_trace_hop(tenant, req.lot_id, req.stage,
                                             req.actor, req.latitude,
                                             req.longitude, req.note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _publish(EVENT_LOT_TRACED_HOP, event)
        return hop

    @app.get("/agri/v1/trace/{lot_id}", tags=["traceability"])
    def trace_lot(lot_id: str, tenant: str = Depends(tenant_from_header),
                  store: AgriStore = Depends(get_store)):
        """Full provenance chain (farm -> ... -> processor/export) with hash
        verification of every hop."""
        try:
            return store.trace(tenant, lot_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    @app.get("/agri/v1/receipt-chain/verify", tags=["warehouse-receipts"])
    def verify_receipt_chain(tenant: str = Depends(tenant_from_header),
                             store: AgriStore = Depends(get_store)):
        errors = store.verify_receipt_chain(tenant)
        return {"tenant_state_id": tenant, "chain_valid": not errors, "errors": errors}

    # --- adapters: prices & telemetry ------------------------------------------------
    @app.get("/agri/v1/prices/{commodity}", response_model=PriceQuote,
             tags=["exchange"])
    def price(commodity: str, tenant: str = Depends(tenant_from_header),
              exchange: CommodityExchangeAdapter = Depends(get_exchange)):
        try:
            return exchange.get_price(tenant, commodity)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except AdapterCallError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.post("/agri/v1/telemetry", status_code=status.HTTP_201_CREATED,
              response_model=TelemetryReading, tags=["iot"])
    def ingest_telemetry(req: TelemetryIngestRequest,
                         tenant: str = Depends(tenant_from_header),
                         store: AgriStore = Depends(get_store),
                         iot: WarehouseIoTAdapter = Depends(get_iot)):
        try:
            state = store.tenant(tenant)
            if req.warehouse_id not in state.warehouses:
                raise KeyError(f"warehouse '{req.warehouse_id}' not found")
            return iot.ingest(tenant, req.warehouse_id, req.moisture_pct,
                              req.temperature_c)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except AdapterCallError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/agri/v1/telemetry/{warehouse_id}",
             response_model=list[TelemetryReading], tags=["iot"])
    def latest_telemetry(warehouse_id: str, tenant: str = Depends(tenant_from_header),
                         iot: WarehouseIoTAdapter = Depends(get_iot)):
        return iot.latest(tenant, warehouse_id)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # --- /metrics: shared exposition + agri gauges --------------------------------
    registry = None
    if _instrument_fastapi is not None:
        try:
            from _shared.observability import LocalRegistry

            registry = LocalRegistry()
        except ImportError:  # pragma: no cover - defensive
            registry = None

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request):
        from starlette.responses import PlainTextResponse

        reg = registry or getattr(request.app.state, "observability_registry", None)
        base = reg.render_prometheus("mod-agri-trace") if reg is not None else ""
        s: AgriStore = request.app.state.store
        receipts = sum(len(t.receipts) for t in s._tenants.values())
        lots = sum(len(t.lots) for t in s._tenants.values())
        extra = (
            "# HELP agri_lots_total Aggregation lots recorded across tenants.\n"
            "# TYPE agri_lots_total gauge\n"
            f"agri_lots_total {lots}\n"
            "# HELP agri_warehouse_receipts_total Warehouse receipts issued.\n"
            "# TYPE agri_warehouse_receipts_total gauge\n"
            f"agri_warehouse_receipts_total {receipts}\n"
        )
        return PlainTextResponse(base + extra)

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-agri-trace", registry=registry)
    return app


app = create_app()
