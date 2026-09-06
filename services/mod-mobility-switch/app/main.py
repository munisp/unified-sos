"""FastAPI surface for mod-mobility-switch (WP-08 / EPIC-10)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from .domain import (
    ClearingRecord,
    FareRule,
    FareTable,
    MobilityStore,
    Mode,
    SettlementBatch,
    cowry_authorize,
    _now,
)


class FareTableRequest(BaseModel):
    gazette_reference: str
    union_commission_pct: float
    fares: list[FareRule]


class TapRequest(BaseModel):
    tenant_state_id: str
    operator_id: str
    mode: Mode
    route: str
    card_ref: str


class CowryAuthRequest(BaseModel):
    card_ref: str
    fare_kobo: int


def get_store(request: Request) -> MobilityStore:
    return request.app.state.store


def create_app(store: MobilityStore | None = None) -> FastAPI:
    app = FastAPI(title="SOS mod-mobility-switch — Multimodal Transit Clearing",
                  version="0.1.0")
    app.state.store = store or MobilityStore()

    @app.put("/mobility/v1/fares/{tenant_state_id}", response_model=FareTable)
    def put_fare_table(tenant_state_id: str, req: FareTableRequest,
                       store: MobilityStore = Depends(get_store)):
        """Configure the state fare table (gazetted; union commission 3–8%)."""
        if not (3.0 <= req.union_commission_pct <= 8.0):
            raise HTTPException(
                status_code=422,
                detail="union_commission_pct outside the gazetted 3–8% band")
        table = FareTable(tenant_state_id=tenant_state_id,
                          gazette_reference=req.gazette_reference,
                          union_commission_pct=req.union_commission_pct,
                          fares=req.fares, updated_at=_now())
        return store.set_fare_table(table)

    @app.get("/mobility/v1/fares/{tenant_state_id}", response_model=FareTable)
    def get_fare_table(tenant_state_id: str, store: MobilityStore = Depends(get_store)):
        table = store.fare_tables.get(tenant_state_id)
        if table is None:
            raise HTTPException(status_code=404,
                                detail=f"no fare table for '{tenant_state_id}'")
        return table

    @app.post("/mobility/v1/clearing", status_code=status.HTTP_201_CREATED,
              response_model=ClearingRecord)
    def record_tap(req: TapRequest, store: MobilityStore = Depends(get_store)):
        """Record a tap/ticket clearing event, priced from the state fare table."""
        try:
            return store.record_tap(req.tenant_state_id, req.operator_id, req.mode,
                                    req.route, req.card_ref)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=exc.args[0])

    @app.post("/mobility/v1/settlements/{tenant_state_id}/{operator_id}",
              status_code=status.HTTP_201_CREATED, response_model=SettlementBatch)
    def settle(tenant_state_id: str, operator_id: str,
               store: MobilityStore = Depends(get_store)):
        """Close an operator settlement batch with TigerBeetle split legs
        (transfer code 140; accounts 4002 union commission, 3001 CRF, 2010 operator)."""
        try:
            return store.settle_operator(tenant_state_id, operator_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/mobility/v1/settlements", response_model=list[SettlementBatch])
    def list_batches(store: MobilityStore = Depends(get_store)):
        return list(store.batches.values())

    @app.post("/mobility/v1/cowry/authorize")
    def cowry_authorization(req: CowryAuthRequest):
        """Cowry-compatible card bridge interface stub (offline-capable)."""
        return cowry_authorize(req.card_ref, req.fare_kobo)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
