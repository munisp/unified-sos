"""FastAPI surface for mod-mortgage — mortgage & lien management.

All endpoints are tenant-scoped: ``/api/v1/states/{state_id}/mortgages/...``
plus the required ``X-State-Tenant`` header, which must match ``state_id``
(fail-closed: missing header → 400; mismatched/foreign tenant → 404).

Fail-closed seams (mirroring mod-ml-inference / mod-safecity-vision):
``SOS_MORTGAGE_PROFILE=production`` hard-fails at boot without
``SOS_MORTGAGE_TB_URL`` and ``SOS_MORTGAGE_LANDS_URL``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .adapters import AdapterUnavailableError, require_production_config
from .domain import (
    ConflictError,
    CrossTenantError,
    InvalidTransitionError,
    Mortgage,
    MortgageStatus,
    MortgageStore,
    NotFoundError,
    Payment,
)
from .outbox import OutboxRelay

# --- shared event bus (services/_shared/eventbus) -----------------------------
try:
    from _shared.eventbus import InMemoryEventBus as _InMemoryEventBus
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _services_root = _Path(__file__).resolve().parents[2]
    if str(_services_root) not in _sys.path:
        _sys.path.insert(0, str(_services_root))
    try:
        from _shared.eventbus import InMemoryEventBus as _InMemoryEventBus
    except ImportError:  # minimal container images ship only the app package
        _InMemoryEventBus = None

# --- Stage 7.C observability wiring (services/_shared/observability.py) ---
try:
    from _shared.observability import LocalRegistry, instrument_fastapi as _instrument_fastapi
except ImportError:
    import sys as _sys2
    from pathlib import Path as _Path2

    _services_root2 = _Path2(__file__).resolve().parents[2]
    if str(_services_root2) not in _sys2.path:
        _sys2.path.insert(0, str(_services_root2))
    try:
        from _shared.observability import LocalRegistry, instrument_fastapi as _instrument_fastapi
    except ImportError:  # minimal container images ship only the app package
        LocalRegistry = None  # type: ignore[assignment]
        _instrument_fastapi = None


class _NullEventBus:
    """Fallback sink when services/_shared is unavailable in the image."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, topic: str, payload) -> None:
        self.events.append((topic, payload))


def get_store(request: Request) -> MortgageStore:
    return request.app.state.store


def tenant_from_header(
    state_id: str, x_state_tenant: Optional[str] = Header(default=None),
) -> str:
    """Tenant scope: path ``state_id`` must agree with ``X-State-Tenant``.

    Fail-closed: missing header → 400; mismatch → 404 (the foreign tenant's
    resources simply do not exist for this caller).
    """
    if not x_state_tenant:
        raise HTTPException(status_code=400, detail="X-State-Tenant header is required")
    tenant = x_state_tenant.lower()
    if tenant != state_id.lower():
        raise HTTPException(status_code=404, detail="tenant mismatch")
    return tenant


class ApplicationIn(BaseModel):
    applicant_id: str
    parcel_id: str
    title_ref: str = Field(..., examples=["LAG-2024-000123"])
    principal_kobo: int = Field(..., gt=0)
    rate_bps: int = Field(..., ge=0, description="annual interest in basis points")
    term_months: int = Field(..., ge=6, le=360)


class ApproveIn(BaseModel):
    officer: str
    reason: str


class LienIn(BaseModel):
    second_charge: bool = False
    senior_lien_id: Optional[str] = None


class PaymentIn(BaseModel):
    amount_kobo: int = Field(..., gt=0)
    idempotency_key: str
    allow_credit: bool = False


class ForecloseIn(BaseModel):
    reason: str


class PossessionIn(BaseModel):
    reference: str = Field(..., description="court/consent reference")


class AuthorizeSaleIn(BaseModel):
    valuation_kobo: int = Field(..., gt=0)
    reserve_price_kobo: int = Field(..., gt=0)
    valuer_id: str


class SellIn(BaseModel):
    purchaser_id: str
    gross_proceeds_kobo: int = Field(..., gt=0)
    sale_costs_kobo: int = Field(0, ge=0)


class MortgageOut(BaseModel):
    mortgage: Mortgage
    paid_to_date_kobo: int
    chain_valid: bool
    audit: list


BASE = "/api/v1/states/{state_id}/mortgages"


def create_app(
    store: Optional[MortgageStore] = None,
    bus=None,
) -> FastAPI:
    """Application factory — inject store/bus for tests. Fail-closed boot."""
    require_production_config()  # SOS_MORTGAGE_PROFILE=production guard

    app = FastAPI(
        title="SOS mod-mortgage — Mortgage & Lien Management",
        version="0.1.0",
        description="Mortgage application → credit scoring → lien registration "
                    "→ ledger disbursement → repayment → discharge/foreclosure "
                    "for state land registries.",
    )
    app.state.store = store or MortgageStore()
    app.state.bus = bus if bus is not None else (
        _InMemoryEventBus() if _InMemoryEventBus is not None else _NullEventBus()
    )
    app.state.metrics = LocalRegistry() if LocalRegistry is not None else None
    app.state.mortgage_counters: dict[str, float] = {}

    # --- transactional outbox relay: the API layer NEVER publishes directly.
    # Domain transitions commit outbox rows atomically inside MortgageStore;
    # the relay publishes and acks. _drain() is a best-effort flush after
    # each request — a publish failure leaves the row un-acked for retry via
    # POST /internal/outbox/relay (or a background relay task).
    from pydantic import BaseModel as _BM

    class _OutboxEnvelope(_BM):
        payload: dict

    class _DictBus:
        def __init__(self, inner) -> None:
            self._inner = inner

        def publish(self, topic: str, payload: dict) -> None:
            self._inner.publish(topic, _OutboxEnvelope(payload=payload))

    app.state.relay = OutboxRelay(app.state.store.outbox, _DictBus(app.state.bus))

    def _drain() -> None:
        app.state.relay.publish_pending_best_effort()

    def _count(metric: str, value: float = 1.0) -> None:
        counters = app.state.mortgage_counters
        counters[metric] = counters.get(metric, 0.0) + value

    def _detail(m: Mortgage, tenant: str) -> MortgageOut:
        audit = app.state.store.audit_feed(tenant, m.mortgage_id)
        valid = not app.state.store.verify_audit_chain(tenant, m.mortgage_id)
        return MortgageOut(mortgage=m, paid_to_date_kobo=m.paid_to_date_kobo,
                           chain_valid=valid, audit=audit)

    def _conflict(exc: Exception):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    # --- application ----------------------------------------------------------
    @app.post(BASE + "/", status_code=status.HTTP_201_CREATED, tags=["mortgage"])
    def apply(body: ApplicationIn, tenant: str = Depends(tenant_from_header),
              store: MortgageStore = Depends(get_store)):
        m = store.apply(tenant, body.applicant_id, body.parcel_id, body.title_ref,
                        body.principal_kobo, body.rate_bps, body.term_months)
        _count("mortgage_applications_total")
        _drain()
        return _detail(m, tenant)

    @app.get(BASE + "/", tags=["mortgage"])
    def list_mortgages(tenant: str = Depends(tenant_from_header),
                       status_filter: Optional[str] = None,
                       applicant_id: Optional[str] = None,
                       parcel_id: Optional[str] = None,
                       store: MortgageStore = Depends(get_store)):
        st = MortgageStatus(status_filter) if status_filter else None
        return [m for m in store.list_mortgages(tenant, st, applicant_id, parcel_id)]

    @app.get(BASE + "/liens", tags=["lien"])
    def list_liens(tenant: str = Depends(tenant_from_header),
                   parcel_id: Optional[str] = None,
                   store: MortgageStore = Depends(get_store)):
        return store.list_liens(tenant, parcel_id)

    @app.get(BASE + "/{mortgage_id}", tags=["mortgage"])
    def get_mortgage(mortgage_id: str, tenant: str = Depends(tenant_from_header),
                     store: MortgageStore = Depends(get_store)):
        try:
            m = store.get_mortgage(tenant, mortgage_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        _drain()  # a lazy default evaluation may have committed outbox rows
        return _detail(m, tenant)

    @app.get(BASE + "/{mortgage_id}/schedule", tags=["mortgage"])
    def get_schedule(mortgage_id: str, tenant: str = Depends(tenant_from_header),
                     store: MortgageStore = Depends(get_store)):
        try:
            m = store.get_mortgage(tenant, mortgage_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        return {
            "mortgage_id": m.mortgage_id,
            "principal_kobo": m.principal_kobo,
            "total_interest_kobo": m.total_interest_kobo,
            "total_kobo": m.principal_kobo + m.total_interest_kobo,
            "installments": m.schedule,
        }

    # --- lifecycle actions ------------------------------------------------------
    @app.post(BASE + "/{mortgage_id}/credit-review", tags=["mortgage"])
    def credit_review(mortgage_id: str, tenant: str = Depends(tenant_from_header),
                      store: MortgageStore = Depends(get_store)):
        try:
            m = store.credit_review(tenant, mortgage_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        if m.status is MortgageStatus.APPROVED:
            _count("mortgage_approvals_total")
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/approve", tags=["mortgage"])
    def approve(mortgage_id: str, body: ApproveIn,
                tenant: str = Depends(tenant_from_header),
                store: MortgageStore = Depends(get_store)):
        try:
            m = store.approve_manual(tenant, mortgage_id, body.officer, body.reason)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        _count("mortgage_approvals_total")
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/register-lien", status_code=status.HTTP_201_CREATED,
              tags=["lien"])
    def register_lien(mortgage_id: str, body: LienIn,
                      tenant: str = Depends(tenant_from_header),
                      store: MortgageStore = Depends(get_store)):
        try:
            lien = store.register_lien(tenant, mortgage_id, body.second_charge,
                                       body.senior_lien_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except (InvalidTransitionError, ConflictError) as exc:
            _conflict(exc)
        _drain()
        return lien

    @app.post(BASE + "/{mortgage_id}/disburse", tags=["mortgage"])
    def disburse(mortgage_id: str, tenant: str = Depends(tenant_from_header),
                 store: MortgageStore = Depends(get_store)):
        try:
            m = store.disburse(tenant, mortgage_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        _count("mortgage_disbursements_total")
        _count("mortgage_kobo_disbursed_total", float(m.principal_kobo))
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/payments", status_code=status.HTTP_201_CREATED,
              tags=["mortgage"])
    def pay(mortgage_id: str, body: PaymentIn,
            tenant: str = Depends(tenant_from_header),
            store: MortgageStore = Depends(get_store)):
        try:
            p = store.apply_payment(tenant, mortgage_id, body.amount_kobo,
                                    body.idempotency_key, body.allow_credit)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except ConflictError as exc:
            _conflict(exc)
        except InvalidTransitionError as exc:
            _conflict(exc)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        _count("mortgage_payments_total")
        _drain()
        return p

    @app.post(BASE + "/{mortgage_id}/foreclose", tags=["mortgage"])
    def foreclose(mortgage_id: str, body: ForecloseIn,
                  tenant: str = Depends(tenant_from_header),
                  store: MortgageStore = Depends(get_store)):
        try:
            m = store.foreclose(tenant, mortgage_id, body.reason)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/possession", tags=["foreclosure"])
    def register_possession(mortgage_id: str, body: PossessionIn,
                            tenant: str = Depends(tenant_from_header),
                            store: MortgageStore = Depends(get_store)):
        try:
            m = store.register_possession(tenant, mortgage_id, body.reference)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/authorize-sale", tags=["foreclosure"])
    def authorize_sale(mortgage_id: str, body: AuthorizeSaleIn,
                       tenant: str = Depends(tenant_from_header),
                       store: MortgageStore = Depends(get_store)):
        try:
            m = store.authorize_sale(tenant, mortgage_id, body.valuation_kobo,
                                     body.reserve_price_kobo, body.valuer_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/sell", tags=["foreclosure"])
    def sell(mortgage_id: str, body: SellIn,
             tenant: str = Depends(tenant_from_header),
             store: MortgageStore = Depends(get_store)):
        try:
            m = store.sell(tenant, mortgage_id, body.purchaser_id,
                           body.gross_proceeds_kobo, body.sale_costs_kobo)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        _drain()
        return _detail(m, tenant)

    @app.post(BASE + "/{mortgage_id}/distribute-proceeds", tags=["foreclosure"])
    def distribute_proceeds(mortgage_id: str,
                            tenant: str = Depends(tenant_from_header),
                            store: MortgageStore = Depends(get_store)):
        try:
            m = store.distribute_proceeds(tenant, mortgage_id)
        except NotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0])
        except InvalidTransitionError as exc:
            _conflict(exc)
        _drain()
        return _detail(m, tenant)

    @app.post("/internal/outbox/relay", tags=["internal"])
    def outbox_relay(request: Request):
        """Explicit relay pass (tests/ops): publish every un-acked outbox
        row and ack on success. At-least-once delivery; consumers dedupe on
        ``idempotency_key``/``event_id``."""
        return {"published": request.app.state.relay.publish_pending()}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Registered BEFORE instrument_fastapi so this exposition (which appends
    # the mortgage counters the shared registry only renders for http_*)
    # wins route matching.
    from fastapi.responses import PlainTextResponse

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> "PlainTextResponse":
        lines: list[str] = []
        if app.state.metrics is not None:
            lines.append(app.state.metrics.render_prometheus("mod-mortgage"))
        for name, value in sorted(app.state.mortgage_counters.items()):
            lines.append(f"# TYPE {name} counter")
            num = int(value) if float(value).is_integer() else value
            lines.append(f'{name}{{service="mod-mortgage"}} {num}')
        return PlainTextResponse("\n".join(lines) + "\n")

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-mortgage", registry=app.state.metrics)
    return app


app = create_app()
