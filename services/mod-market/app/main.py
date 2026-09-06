"""mod-market FastAPI application."""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .models import (
    DisputeEvent,
    EdgeSyncBatch,
    IngestedAck,
    Market,
    Stall,
    StallageTicket,
    Trader,
    TraderRead,
)
from .service import ConflictError, MarketService, NotFoundError


class IssueTicketRequest(BaseModel):
    stall_id: str
    service_date: date
    trader_id: Optional[str] = None


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


def create_app(service: Optional[MarketService] = None) -> FastAPI:
    app = FastAPI(title="mod-market — Commercial Markets & Digital Stall Titling")
    app.state.service = service or MarketService()

    def svc(request: Request) -> MarketService:
        return request.app.state.service

    def _map(exc: Exception):
        if isinstance(exc, NotFoundError):
            return HTTPException(404, str(exc))
        if isinstance(exc, (ConflictError, ValueError)):
            return HTTPException(409, str(exc))
        return exc

    @app.post("/markets", response_model=Market, status_code=201)
    def register_market(market: Market, request: Request):
        try:
            return svc(request).register_market(market)
        except (ConflictError, NotFoundError) as exc:
            raise _map(exc)

    @app.post("/stalls", response_model=Stall, status_code=201)
    def register_stall(stall: Stall, request: Request):
        try:
            return svc(request).register_stall(stall)
        except (ConflictError, NotFoundError) as exc:
            raise _map(exc)

    @app.post("/traders", response_model=TraderRead, status_code=201)
    def enumerate_trader(trader: Trader, request: Request):
        try:
            return svc(request).enumerate_trader(trader)
        except NotFoundError as exc:
            raise _map(exc)

    @app.get("/traders", response_model=List[TraderRead])
    def list_traders(request: Request, market_id: Optional[str] = None):
        return svc(request).list_traders(market_id)

    @app.post("/tickets", response_model=StallageTicket, status_code=201)
    def issue_ticket(body: IssueTicketRequest, request: Request):
        try:
            return svc(request).issue_ticket(
                body.stall_id, body.service_date, body.trader_id
            )
        except (ConflictError, NotFoundError) as exc:
            raise _map(exc)

    @app.get("/tickets/{ticket_id}", response_model=StallageTicket)
    def get_ticket(ticket_id: str, request: Request):
        try:
            return svc(request).get_ticket(ticket_id)
        except NotFoundError as exc:
            raise _map(exc)

    @app.post("/tickets/ingest-edge-batch", response_model=List[IngestedAck])
    def ingest_edge_batch(batch: EdgeSyncBatch, request: Request):
        """Ingest signed offline stallage tickets from an edge-daemon batch."""
        return svc(request).ingest_edge_batch(batch)

    @app.post("/disputes/events", response_model=DisputeEvent, status_code=201)
    def record_dispute_event(event: DisputeEvent, request: Request):
        try:
            return svc(request).record_dispute_event(event)
        except (ConflictError, NotFoundError) as exc:
            raise _map(exc)

    @app.get("/disputes/{ticket_id}/trail", response_model=List[DisputeEvent])
    def dispute_trail(ticket_id: str, request: Request):
        try:
            return svc(request).dispute_trail(ticket_id)
        except NotFoundError as exc:
            raise _map(exc)

    @app.get("/health")
    def health():
        return {"status": "ok", "module": "mod-market"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-market")
    return app


app = create_app()
