"""FastAPI surface for mod-waterways — Inland Waterways Ferry E-Ticketing &
Sand-Dredging Volumetric Monitoring.

All domain endpoints are tenant-scoped via the ``X-State-Tenant`` header
(HTTP 400 when missing). Fail-closed adapters (Sedona volumetrics, AIS) are
selected by ``SOS_WATERWAYS_PROFILE`` at boot.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .adapters import (
    SedonaVolumetricsAdapter,
    VesselAisAdapter,
    build_ais_adapter,
    build_sedona_adapter,
)
from .domain import (
    ADOPTION_STATES,
    FARE_POLICIES,
    CrossTenantError,
    Dredger,
    ManifestLockedError,
    RoyaltyAssessment,
    SoldOutError,
    Survey,
    Ticket,
    Trip,
    WaterwaysStore,
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

# --- Shared event bus (import-guard idiom; memory default) -------------------
try:
    from _shared.eventbus import InMemoryEventBus as _InMemoryEventBus
except ImportError:
    _InMemoryEventBus = None


class _NullEventBus:
    """Fallback bus for minimal container images: records nothing, never fails
    (fixture profile only — production images ship services/_shared)."""

    def __init__(self) -> None:
        self.published: list = []

    def publish(self, topic: str, payload: BaseModel) -> None:
        self.published.append({"topic": topic, "payload": payload.model_dump(mode="json")})


# --- request models -----------------------------------------------------------


class TripScheduleRequest(BaseModel):
    route_id: str
    vessel: str
    capacity: int = Field(..., gt=0)
    departure: str = Field(..., description="ISO-8601 scheduled departure")


class TicketPurchaseRequest(BaseModel):
    trip_id: str
    passenger_name: str = Field(..., min_length=1)


class DredgerRegistrationRequest(BaseModel):
    vessel_name: str
    license_no: str
    operator_kyb_ref: str = Field(..., description="mod-kyc-kyb business verification ref")
    monthly_quota_m3: float = Field(..., gt=0)


class SurveyIngestRequest(BaseModel):
    dredger_id: str
    polygon: list[list[float]] = Field(..., description="[lon, lat] ring")
    volume_m3: float = Field(..., gt=0)
    surveyed_at: str = Field(..., description="ISO-8601 survey timestamp")
    depth_m: float = Field(3.0, gt=0, description="bathymetric depth for verification")


# --- dependencies -----------------------------------------------------------------


def get_store(request: Request) -> WaterwaysStore:
    return request.app.state.store


def get_sedona(request: Request) -> SedonaVolumetricsAdapter:
    return request.app.state.sedona


def get_ais(request: Request) -> VesselAisAdapter:
    return request.app.state.ais


def require_tenant(x_state_tenant: str | None = Header(default=None)) -> str:
    """Tenant scoping: every domain endpoint requires X-State-Tenant."""
    if not x_state_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-State-Tenant header is required",
        )
    tenant = x_state_tenant.strip().lower()
    if tenant not in ADOPTION_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown state tenant '{x_state_tenant}'; adoption states: "
                   f"{', '.join(ADOPTION_STATES)}",
        )
    return tenant


def create_app(store: WaterwaysStore | None = None,
               sedona: SedonaVolumetricsAdapter | None = None,
               ais: VesselAisAdapter | None = None) -> FastAPI:
    app = FastAPI(
        title="SOS mod-waterways — Inland Waterways Ferry E-Ticketing & Dredging Volumetrics",
        version="0.1.0",
        description="Ferry e-ticketing (integer-kobo fares, QR refs, manifest "
                    "locked at departure) and sand-dredging volumetric "
                    "monitoring (quota alerts, hash-chained royalty levies).",
    )
    bus = (_InMemoryEventBus() if _InMemoryEventBus is not None else None) or _NullEventBus()
    app.state.event_bus = bus
    app.state.store = store or WaterwaysStore(event_bus=bus)
    if store is not None and store.event_bus is None:
        store.event_bus = bus
    # Fail-closed adapter bindings: default fixture profile is deterministic;
    # SOS_WATERWAYS_PROFILE=production hard-fails here at boot without config.
    app.state.sedona = sedona or build_sedona_adapter()
    app.state.ais = ais or build_ais_adapter()

    # --- routes / jetties -----------------------------------------------------
    @app.get("/waterways/v1/routes", tags=["registry"])
    def list_routes(tenant: str = Depends(require_tenant),
                    store: WaterwaysStore = Depends(get_store)):
        return [r.model_dump() for r in store.list_routes(tenant)]

    @app.get("/waterways/v1/jetties", tags=["registry"])
    def list_jetties(tenant: str = Depends(require_tenant),
                     store: WaterwaysStore = Depends(get_store)):
        return {"tenant_state_id": tenant, "jetties": store.list_jetties(tenant)}

    @app.get("/waterways/v1/fare-policy", tags=["registry"])
    def fare_policy(tenant: str = Depends(require_tenant)):
        return FARE_POLICIES[tenant].model_dump()

    # --- trips / tickets --------------------------------------------------------
    @app.post("/waterways/v1/trips", status_code=status.HTTP_201_CREATED,
              response_model=Trip, tags=["ticketing"])
    def schedule_trip(req: TripScheduleRequest, tenant: str = Depends(require_tenant),
                      store: WaterwaysStore = Depends(get_store)):
        try:
            return store.schedule_trip(tenant, req.route_id, req.vessel,
                                       req.capacity, req.departure)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])

    @app.get("/waterways/v1/trips", response_model=list[Trip], tags=["ticketing"])
    def list_trips(tenant: str = Depends(require_tenant),
                   store: WaterwaysStore = Depends(get_store)):
        return store.list_trips(tenant)

    @app.post("/waterways/v1/tickets", status_code=status.HTTP_201_CREATED,
              response_model=Ticket, tags=["ticketing"])
    def purchase_ticket(req: TicketPurchaseRequest, tenant: str = Depends(require_tenant),
                        store: WaterwaysStore = Depends(get_store)):
        try:
            return store.purchase_ticket(tenant, req.trip_id, req.passenger_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (SoldOutError, ManifestLockedError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    @app.get("/waterways/v1/tickets", response_model=list[Ticket], tags=["ticketing"])
    def list_tickets(trip_id: str | None = None, tenant: str = Depends(require_tenant),
                     store: WaterwaysStore = Depends(get_store)):
        return store.list_tickets(tenant, trip_id)

    @app.get("/waterways/v1/trips/{trip_id}/manifest", tags=["ticketing"])
    def trip_manifest(trip_id: str, tenant: str = Depends(require_tenant),
                      store: WaterwaysStore = Depends(get_store)):
        try:
            return store.manifest(tenant, trip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.post("/waterways/v1/trips/{trip_id}/depart", tags=["ticketing"])
    def depart_trip(trip_id: str, tenant: str = Depends(require_tenant),
                    store: WaterwaysStore = Depends(get_store)):
        """Mark departure — safety rule: manifest locks at departure."""
        try:
            trip = store.depart_trip(tenant, trip_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ManifestLockedError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        return trip

    # --- dredgers ----------------------------------------------------------------
    @app.post("/waterways/v1/dredgers", status_code=status.HTTP_201_CREATED,
              response_model=Dredger, tags=["dredging"])
    def register_dredger(req: DredgerRegistrationRequest,
                         tenant: str = Depends(require_tenant),
                         store: WaterwaysStore = Depends(get_store)):
        return store.register_dredger(tenant, req.vessel_name, req.license_no,
                                      req.operator_kyb_ref, req.monthly_quota_m3)

    @app.get("/waterways/v1/dredgers", response_model=list[Dredger], tags=["dredging"])
    def list_dredgers(tenant: str = Depends(require_tenant),
                      store: WaterwaysStore = Depends(get_store)):
        return store.list_dredgers(tenant)

    @app.get("/waterways/v1/dredgers/{dredger_id}/position", tags=["dredging"])
    def dredger_position(dredger_id: str, mmsi: str,
                         tenant: str = Depends(require_tenant),
                         store: WaterwaysStore = Depends(get_store),
                         ais: VesselAisAdapter = Depends(get_ais)):
        """Latest AIS fix for a registered dredger vessel (fixture/live seam)."""
        dredger = next((d for d in store.list_dredgers(tenant)
                        if d.dredger_id == dredger_id), None)
        if dredger is None:
            raise HTTPException(status_code=404,
                                detail=f"dredger '{dredger_id}' not found")
        try:
            position = ais.latest_position(mmsi)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return position.__dict__

    # --- surveys / quotas / royalties ----------------------------------------------
    @app.post("/waterways/v1/surveys", status_code=status.HTTP_201_CREATED,
              tags=["dredging"])
    def ingest_survey(req: SurveyIngestRequest, tenant: str = Depends(require_tenant),
                      store: WaterwaysStore = Depends(get_store),
                      sedona: SedonaVolumetricsAdapter = Depends(get_sedona)):
        """Ingest a volumetric dredging survey. Cross-checks the claimed volume
        against the Sedona volumetric adapter, rolls into the monthly quota,
        and computes a hash-chained royalty levy (integer kobo per m³)."""
        try:
            verification = sedona.verify_volume(req.polygon, req.depth_m)
            survey, assessment, over_quota = store.ingest_survey(
                tenant, req.dredger_id, req.polygon, req.volume_m3,
                req.surveyed_at, verified_volume_m3=verification.verified_volume_m3,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except CrossTenantError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "survey": survey.model_dump(),
            "royalty_assessment": assessment.model_dump(),
            "over_quota": over_quota,
        }

    @app.get("/waterways/v1/surveys", response_model=list[Survey], tags=["dredging"])
    def list_surveys(tenant: str = Depends(require_tenant),
                     store: WaterwaysStore = Depends(get_store)):
        return [s for s in store.surveys.values() if s.tenant_state_id == tenant]

    @app.get("/waterways/v1/quotas", tags=["dredging"])
    def quota_status(month: str | None = None, tenant: str = Depends(require_tenant),
                     store: WaterwaysStore = Depends(get_store)):
        """Monthly cumulative volume per dredger vs licensed quota."""
        return store.quota_status(tenant, month)

    @app.get("/waterways/v1/royalties", tags=["dredging"])
    def list_royalties(tenant: str = Depends(require_tenant),
                       store: WaterwaysStore = Depends(get_store)):
        """Hash-chained royalty assessment audit feed for the tenant."""
        return {
            "tenant_state_id": tenant,
            "chain_errors": store.verify_royalty_chain(tenant),
            "assessments": store.list_royalties(tenant),
        }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-waterways")
    return app


app = create_app()
