"""FastAPI surface for mod-mobility-switch (WP-08 / EPIC-10)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .adapters.base import AdapterUnavailableError
from .domain import (
    BillEventConflict,
    ClearingRecord,
    EscrowExpiredError,
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


class PaymentQuoteRequest(BaseModel):
    """Citizen-PWA payment quote request.

    ``bill_reference`` is the canonical field; ``ticket_ref`` is accepted as
    an alias for the PWA's fare-ticket flow.
    """

    bill_reference: str | None = None
    ticket_ref: str | None = None
    amount_kobo: int = Field(..., gt=0)
    payer: str = "payer"

    @property
    def reference(self) -> str:
        ref = self.bill_reference or self.ticket_ref
        if not ref:
            raise ValueError("bill_reference (or ticket_ref) is required")
        return ref


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
    # tenant_state_id -> quote_id -> quote record (payment quotes seam)
    app.state.payment_quotes = {}

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
        except AdapterUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
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
        /transfers prepare to the scheme. Idempotent on transfer_id.

        Ordering: the local record is created FIRST; if the scheme call then
        fails, the local escrow is compensated (aborted) so no orphaned
        scheme-side prepare can exist without a local record."""
        fspiop = request.app.state.fspiop
        replay = req.transfer_id in store.escrows
        try:
            rec = store.begin_escrow(req.transfer_id, req.batch_id,
                                     req.amount_kobo, req.condition)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if fspiop is not None and not replay:
            try:
                fspiop.transfer_prepare(req.transfer_id, req.amount_kobo,
                                        req.condition, rec.expires_at)
            except AdapterUnavailableError as exc:
                store.abort_escrow(req.transfer_id)  # compensating abort
                raise HTTPException(status_code=503, detail=str(exc))
            except Exception as exc:  # scheme-side failure → compensate
                store.abort_escrow(req.transfer_id)
                raise HTTPException(status_code=502,
                                    detail=f"scheme prepare failed: {exc}")
        return rec

    @app.post("/mobility/v1/escrow/sweep", response_model=list[EscrowRecord])
    def sweep_escrows(store: MobilityStore = Depends(get_store)):
        """Auto-abort every expired PENDING escrow (idempotent sweep)."""
        return store.sweep_expired_escrows()

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
        except (EscrowExpiredError, ValueError) as exc:
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
        try:
            event = store.record_bill_event(req.bill_reference, {
                "bill_reference": req.bill_reference,
                "amount_kobo": req.amount_kobo,
                "channel": req.channel,
                "provider_reference": req.provider_reference,
                "received_at": _now(),
            })
        except BillEventConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"status": "recorded", "event": event}

    @app.get("/mobility/v1/audit")
    def audit_feed(store: MobilityStore = Depends(get_store)):
        """Hash-chained audit feed (bill events, conflicts, settlements)."""
        return {"chain_errors": store.verify_audit_chain(),
                "events": store.audit_chain}

    @app.post("/mobility/v1/cowry/authorize")
    def cowry_authorization(req: CowryAuthRequest):
        """Cowry-compatible card bridge interface stub (offline-capable)."""
        return cowry_authorize(req.card_ref, req.fare_kobo)

    # --- citizen payment quotes (/payments/v1/*) -----------------------------
    # Serves the citizen-PWA payment flow (apps/citizen-pwa/src/lib/api.ts):
    # quote → confirm. Fees come from the FSPIOP adapter seam when one is
    # wired; otherwise the deterministic fixture fee (1% + ₦10.00, the same
    # formula as FixtureFspiopAdapter.quote) is used. Confirm executes the
    # settlement through the existing FSPIOP prepare → fulfil path (fixture
    # in-memory when no scheme adapter is wired), idempotent on
    # Idempotency-Key; expired quotes return 410. All quotes are
    # tenant-scoped via the required X-State-Tenant header.

    QUOTE_TTL_SECONDS = 600

    def _payments_tenant(x_state_tenant: str | None = Header(default=None)) -> str:
        if not x_state_tenant:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-State-Tenant header is required for payment endpoints")
        return x_state_tenant

    def _quote_fee(request: Request, quote_id: str, amount_kobo: int,
                   payer: str, tenant: str) -> int:
        fspiop = request.app.state.fspiop
        if fspiop is not None:
            try:
                body = fspiop.quote(quote_id, amount_kobo, payer, tenant)
            except AdapterUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            return int(body.get("payeeFspFeeMinor") or 0)
        return amount_kobo // 100 + 1000  # deterministic fixture fee

    def _quote_expired(quote: dict) -> bool:
        return (datetime.now(timezone.utc)
                > datetime.fromisoformat(quote["expires_at"]))

    @app.post("/payments/v1/quotes", status_code=status.HTTP_201_CREATED)
    def create_payment_quote(req: PaymentQuoteRequest, request: Request,
                             tenant: str = Depends(_payments_tenant)):
        try:
            reference = req.reference
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=exc.args[0])
        digest = hashlib.sha256(
            f"payment-quote:{tenant}:{reference}:{req.amount_kobo}:{req.payer}"
            .encode("utf-8")).hexdigest()[:12].upper()
        quote_id = f"QTE-{digest}"
        tenant_quotes = request.app.state.payment_quotes.setdefault(tenant, {})
        existing = tenant_quotes.get(quote_id)
        if existing is not None and not _quote_expired(existing):
            return existing  # idempotent replay of the same quote inputs
        expires_at = (datetime.now(timezone.utc)
                      + timedelta(seconds=QUOTE_TTL_SECONDS)).isoformat(timespec="seconds")
        quote = {
            "quote_id": quote_id,
            "bill_reference": reference,
            "amount_kobo": req.amount_kobo,
            "fees_kobo": _quote_fee(request, quote_id, req.amount_kobo,
                                    req.payer, tenant),
            "payer": req.payer,
            "tenant_state_id": tenant,
            "status": "PENDING",
            "expires_at": expires_at,
        }
        tenant_quotes[quote_id] = quote
        return quote

    @app.post("/payments/v1/quotes/{quote_id}/confirm")
    def confirm_payment_quote(quote_id: str, request: Request,
                              idempotency_key: str | None = Header(
                                  default=None, alias="Idempotency-Key"),
                              tenant: str = Depends(_payments_tenant)):
        tenant_quotes = request.app.state.payment_quotes.get(tenant, {})
        quote = tenant_quotes.get(quote_id)
        if quote is None:
            raise HTTPException(status_code=404,
                                detail=f"quote '{quote_id}' not found")
        if quote["status"] == "COMPLETED":
            # Idempotent replay only with the same Idempotency-Key.
            if idempotency_key and idempotency_key == quote.get("idempotency_key"):
                return quote
            raise HTTPException(
                status_code=409,
                detail=f"quote '{quote_id}' already settled "
                       "(Idempotency-Key required for replay)")
        if _quote_expired(quote):
            raise HTTPException(status_code=410,
                                detail=f"quote '{quote_id}' expired at "
                                       f"{quote['expires_at']}")
        # Execute settlement through the FSPIOP prepare → fulfil seam (the
        # existing escrow settle path); fixture in-memory when unconfigured.
        fspiop = request.app.state.fspiop
        total_kobo = quote["amount_kobo"] + quote["fees_kobo"]
        if fspiop is not None:
            try:
                fspiop.transfer_prepare(quote_id, total_kobo, "",
                                        quote["expires_at"])
                fulfilment = fspiop.transfer_fulfil(quote_id, "")["fulfilment"]
            except AdapterUnavailableError as exc:
                raise HTTPException(status_code=503, detail=str(exc))
            except KeyError as exc:
                raise HTTPException(status_code=502, detail=exc.args[0])
            except Exception as exc:
                raise HTTPException(status_code=502,
                                    detail=f"scheme settlement failed: {exc}")
        else:
            fulfilment = hashlib.sha256(
                f"fixture-fulfil:{quote_id}".encode("utf-8")).hexdigest()
        quote.update({
            "status": "COMPLETED",
            "settled_amount_kobo": total_kobo,
            "fulfilment": fulfilment,
            "idempotency_key": idempotency_key,
            "settled_at": _now(),
        })
        return quote

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-mobility-switch")
    return app


app = create_app()
