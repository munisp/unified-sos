"""FastAPI surface for mod-mobility-switch (WP-08 / EPIC-10)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from .adapters.base import AdapterUnavailableError
from .domain import (
    ClearingRecord,
    EscrowRecord,
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


class EscrowPrepareRequest(BaseModel):
    transfer_id: str
    batch_id: str
    amount_kobo: int
    condition: str = ""


class NibssBillNotification(BaseModel):
    bill_reference: str
    amount_kobo: int
    channel: str = "NIP"
    provider_reference: str = ""


def get_store(request: Request) -> MobilityStore:
    return request.app.state.store


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


def create_app(store: MobilityStore | None = None, fspiop=None, nibss=None) -> FastAPI:
    """App factory. `fspiop` / `nibss` are scheme adapters (FSPIOP / NIBSS
    e-Bills); when None the scheme seams fail closed on use."""
    app = FastAPI(title="SOS mod-mobility-switch — Multimodal Transit Clearing",
                  version="0.1.0")
    app.state.store = store or MobilityStore()
    app.state.fspiop = fspiop
    app.state.nibss = nibss

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

    # --- pending-transfer escrow (Mojaloop FSPIOP seam) ----------------------
    @app.post("/mobility/v1/escrow", status_code=status.HTTP_201_CREATED,
              response_model=EscrowRecord)
    def prepare_escrow(req: EscrowPrepareRequest, request: Request,
                       store: MobilityStore = Depends(get_store)):
        """Begin a pending-transfer escrow: reserves the batch gross on the
        escrow account and (when an FSPIOP adapter is wired) POSTs
        /transfers prepare to the scheme. Idempotent on transfer_id."""
        fspiop = request.app.state.fspiop
        if fspiop is not None:
            try:
                fspiop.transfer_prepare(req.transfer_id, req.amount_kobo,
                                        req.condition, "")
            except AdapterUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
        try:
            return store.begin_escrow(req.transfer_id, req.batch_id,
                                      req.amount_kobo, req.condition)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/mobility/v1/escrow/{transfer_id}/fulfil", response_model=EscrowRecord)
    def fulfil_escrow(transfer_id: str, request: Request,
                      store: MobilityStore = Depends(get_store)):
        """Post a pending escrow (FSPIOP COMMITTED fulfilment)."""
        fspiop = request.app.state.fspiop
        fulfilment = ""
        if fspiop is not None:
            try:
                fulfilment = fspiop.transfer_fulfil(transfer_id, "")["fulfilment"]
            except AdapterUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=exc.args[0])
        try:
            return store.fulfil_escrow(transfer_id, fulfilment)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/mobility/v1/escrow/{transfer_id}/abort", response_model=EscrowRecord)
    def abort_escrow(transfer_id: str, store: MobilityStore = Depends(get_store)):
        """Void a pending escrow (FSPIOP ABORTED / expiry)."""
        try:
            return store.abort_escrow(transfer_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/mobility/v1/webhooks/nibss/ebills")
    def nibss_bill_notification(req: NibssBillNotification, request: Request,
                                store: MobilityStore = Depends(get_store)):
        """NIBSS e-Bills payment notification; HMAC signature checked via the
        adapter. Idempotent on bill_reference. Without a configured adapter
        the seam fails closed (503) — no unsigned notifications accepted."""
        nibss = request.app.state.nibss
        if nibss is None:
            raise HTTPException(
                status_code=503,
                detail="NIBSS e-Bills adapter not configured (fail closed)")
        body = req.model_dump_json().encode("utf-8")
        if not nibss.verify_notification_signature(request.headers, body):
            raise HTTPException(status_code=401, detail="invalid NIBSS signature")
        event = store.record_bill_event(req.bill_reference, {
            "bill_reference": req.bill_reference,
            "amount_kobo": req.amount_kobo,
            "channel": req.channel,
            "provider_reference": req.provider_reference,
            "received_at": _now(),
        })
        return {"status": "recorded", "event": event}

    @app.post("/mobility/v1/cowry/authorize")
    def cowry_authorization(req: CowryAuthRequest):
        """Cowry-compatible card bridge interface stub (offline-capable)."""
        return cowry_authorize(req.card_ref, req.fare_kobo)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-mobility-switch")
    return app


app = create_app()
