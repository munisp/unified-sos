"""FastAPI surface for mod-police-cad (WP-13 / EPIC-15).

Ratification-independent endpoints (incident intake, unit registry, geofenced
dispatch, trust-fund transparency) are always available. Endpoints tagged
``ratification_gated`` (arms register, state-force stand-up) are locked behind
the constitutional RatificationGate and return HTTP 423 with the legal basis
until ratification (24-of-36 assemblies) + presidential assent are recorded.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from .domain import (
    CadStore,
    CrossTenantError,
    DispatchEvent,
    Disbursement,
    Donation,
    Incident,
    Unit,
)
from .gate import GatedError, RatificationGate
from .wazuh import WazuhAdapter, build_wazuh_adapter
from .webrtc import StreamSession, WebRTCGateway, build_webrtc_gateway


class IncidentIntake(BaseModel):
    tenant_state_id: str
    agency: str = Field(..., examples=["AMOTEKUN", "SO-SAFE", "BSCPG", "LNSC", "NPF"])
    category: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class UnitRegistration(BaseModel):
    tenant_state_id: str
    agency: str
    call_sign: str
    personnel_count: int = Field(..., ge=0)
    biometric_enrolled: bool = Field(
        ..., description="Biometric enrolment complete (ghost-worker payroll control)"
    )


class DispatchRequest(BaseModel):
    incident_id: str
    unit_id: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class DonationRequest(BaseModel):
    tenant_state_id: str
    donor_ref: str
    amount_kobo: int = Field(..., gt=0)


class DisbursementRequest(BaseModel):
    tenant_state_id: str
    purpose: str
    amount_kobo: int = Field(..., gt=0)


class StreamSessionRequest(BaseModel):
    tenant_state_id: str
    kind: str = Field(..., pattern="^(cctv|drone)$")
    sdp_offer: str = Field(..., description="WebRTC SDP offer from the camera/drone peer")


class ArmsEntryRequest(BaseModel):
    """ratification_gated: arms-register entry for a certified state force."""

    tenant_state_id: str
    serial_no: str
    weapon_type: str
    assigned_unit_id: str


class StandUpRequest(BaseModel):
    """ratification_gated: state-force stand-up order."""

    tenant_state_id: str
    force_name: str
    enabling_law_ref: str
    initial_strength: int = Field(..., ge=0)


def get_store(request: Request) -> CadStore:
    return request.app.state.store


def get_gate(request: Request) -> RatificationGate:
    return request.app.state.gate


def get_webrtc(request: Request) -> WebRTCGateway:
    return request.app.state.webrtc


def get_wazuh(request: Request) -> WazuhAdapter:
    return request.app.state.wazuh


def _forward(wazuh: WazuhAdapter, event: dict) -> None:
    """Forward a security-relevant event to the SIEM. Fail-closed: a
    production SIEM outage surfaces as an error rather than a silently
    dropped security event."""
    wazuh.forward_event(event)


def _require_ratified(gate: RatificationGate, wazuh: WazuhAdapter,
                      category: str, tenant_state_id: str) -> None:
    try:
        gate.check()
    except GatedError as exc:
        # Gate denials are security-relevant transitions: SIEM-visible.
        _forward(wazuh, {
            "event_type": "gate_denial",
            "category": category,
            "tenant_state_id": tenant_state_id,
            "gate": "SOS_POLICE_RATIFIED",
            "legal_basis": exc.legal_basis,
        })
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": "ratification_gated",
                "legal_basis": exc.legal_basis,
                "gate": "SOS_POLICE_RATIFIED",
            },
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


def create_app(store: CadStore | None = None,
               gate: RatificationGate | None = None,
               webrtc: WebRTCGateway | None = None,
               wazuh: WazuhAdapter | None = None) -> FastAPI:
    app = FastAPI(
        title="SOS mod-police-cad — Public Safety & Emergency Dispatch CAD",
        version="0.1.0",
        description="Community vigilante CAD operational immediately; state-police "
                    "operational modules gated by constitutional ratification.",
    )
    app.state.store = store or CadStore()
    app.state.gate = gate or RatificationGate.from_env()
    # Fail-closed adapter bindings: default fixture profile is deterministic;
    # SOS_CAD_PROFILE=production hard-fails here at boot without config.
    app.state.webrtc = webrtc or build_webrtc_gateway()
    app.state.wazuh = wazuh or build_wazuh_adapter()

    # --- ratification-INDEPENDENT -------------------------------------------
    @app.post("/cad/v1/incidents", status_code=status.HTTP_201_CREATED,
              response_model=Incident, tags=["cad"])
    def intake_incident(req: IncidentIntake, store: CadStore = Depends(get_store),
                        wazuh: WazuhAdapter = Depends(get_wazuh)):
        try:
            incident = store.intake_incident(req.tenant_state_id, req.agency, req.category,
                                             req.latitude, req.longitude)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _forward(wazuh, {
            "event_type": "incident_created",
            "incident_id": incident.incident_id,
            "tenant_state_id": incident.tenant_state_id,
            "agency": incident.agency,
            "category": incident.category,
        })
        return incident

    @app.get("/cad/v1/incidents", response_model=list[Incident], tags=["cad"])
    def list_incidents(store: CadStore = Depends(get_store)):
        return list(store.incidents.values())

    @app.post("/cad/v1/units", status_code=status.HTTP_201_CREATED,
              response_model=Unit, tags=["registry"])
    def register_unit(req: UnitRegistration, store: CadStore = Depends(get_store)):
        return store.register_unit(req.tenant_state_id, req.agency, req.call_sign,
                                   req.personnel_count, req.biometric_enrolled)

    @app.get("/cad/v1/units", response_model=list[Unit], tags=["registry"])
    def list_units(store: CadStore = Depends(get_store)):
        return list(store.units.values())

    @app.post("/cad/v1/dispatch", status_code=status.HTTP_201_CREATED,
              response_model=DispatchEvent, tags=["cad"])
    def dispatch(req: DispatchRequest, store: CadStore = Depends(get_store),
                 wazuh: WazuhAdapter = Depends(get_wazuh)):
        """Geofenced dispatch: the event is logged only inside the state geofence."""
        try:
            event = store.dispatch(req.incident_id, req.unit_id, req.latitude, req.longitude)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0])
        except CrossTenantError as exc:
            # Tenant-isolation violation: fail closed with 403 (matching
            # mod-ppp-investment TenantIsolationError) — never a 2xx/422.
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        incident = store.incidents[req.incident_id]
        _forward(wazuh, {
            "event_type": "dispatch_created",
            "event_id": event.event_id,
            "incident_id": event.incident_id,
            "unit_id": event.unit_id,
            "tenant_state_id": incident.tenant_state_id,
        })
        return event

    @app.get("/cad/v1/dispatch-log", response_model=list[DispatchEvent], tags=["cad"])
    def dispatch_log(store: CadStore = Depends(get_store)):
        return list(store.dispatch_log)

    @app.get("/cad/v1/dispatch/slo", tags=["cad"])
    def dispatch_slo(store: CadStore = Depends(get_store)):
        """Dispatch-latency SLO status: incident-created -> dispatch-assigned.

        p50/p95 (seconds, linear interpolation) plus the count of dispatches
        breaching the <30 s acceptance SLO."""
        return store.latency.summary()

    # --- CCTV/drone stream sessions (WebRTC gateway binding) ------------------
    @app.post("/cad/v1/streams/{camera_id}/session",
              status_code=status.HTTP_201_CREATED, tags=["streams"])
    def open_stream_session(camera_id: str, req: StreamSessionRequest,
                            store: CadStore = Depends(get_store),
                            gateway: WebRTCGateway = Depends(get_webrtc),
                            wazuh: WazuhAdapter = Depends(get_wazuh)):
        """Establish a WebRTC session for a CCTV/drone feed. Stream metadata
        (camera id, tenant, kind, started_at) is recorded to the domain store
        and forwarded to the SIEM; media never transits this service."""
        try:
            session = gateway.create_stream_session(
                camera_id, req.tenant_state_id, req.kind, req.sdp_offer
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record = store.record_stream_session(
            session.session_id, session.camera_id, session.tenant_state_id,
            session.kind, session.started_at,
        )
        _forward(wazuh, {"event_type": "stream_session_opened", **record})
        return {**record, "sdp_answer": session.sdp_answer}

    @app.get("/cad/v1/streams", tags=["streams"])
    def list_stream_sessions(tenant_state_id: str | None = None,
                             store: CadStore = Depends(get_store)):
        """List active stream sessions; ``tenant_state_id`` filter preserves
        tenant isolation for per-state dashboards."""
        return store.list_stream_sessions(tenant_state_id)

    @app.delete("/cad/v1/streams/{session_id}", tags=["streams"])
    def close_stream_session(session_id: str, store: CadStore = Depends(get_store),
                             gateway: WebRTCGateway = Depends(get_webrtc),
                             wazuh: WazuhAdapter = Depends(get_wazuh)):
        record = store.close_stream_session(session_id)
        if record is None:
            raise HTTPException(status_code=404,
                                detail=f"stream session '{session_id}' not found")
        gateway.close_session(session_id)
        _forward(wazuh, {"event_type": "stream_session_closed", **record})
        return {"closed": True, "session_id": session_id}

    @app.post("/cad/v1/trust-fund/donations", status_code=status.HTTP_201_CREATED,
              response_model=Donation, tags=["trust-fund"])
    def donate(req: DonationRequest, store: CadStore = Depends(get_store)):
        return store.record_donation(req.tenant_state_id, req.donor_ref, req.amount_kobo)

    @app.post("/cad/v1/trust-fund/disbursements", status_code=status.HTTP_201_CREATED,
              response_model=Disbursement, tags=["trust-fund"])
    def disburse(req: DisbursementRequest, store: CadStore = Depends(get_store)):
        try:
            return store.record_disbursement(req.tenant_state_id, req.purpose, req.amount_kobo)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/cad/v1/trust-fund/{tenant_state_id}/audit-feed", tags=["trust-fund"])
    def audit_feed(tenant_state_id: str, store: CadStore = Depends(get_store)):
        """Public transparency feed — no auth boundary in the reference build."""
        return store.public_audit_feed(tenant_state_id)

    # --- ratification-GATED ----------------------------------------------------
    @app.post("/cad/v1/arms-register", status_code=status.HTTP_201_CREATED,
              tags=["ratification_gated"])
    def arms_register(req: ArmsEntryRequest, store: CadStore = Depends(get_store),
                      gate: RatificationGate = Depends(get_gate),
                      wazuh: WazuhAdapter = Depends(get_wazuh)):
        """Arms register — state-force armory tracking. GATED pre-ratification."""
        _forward(wazuh, {
            "event_type": "arms_register_attempt",
            "tenant_state_id": req.tenant_state_id,
            "serial_no": req.serial_no,
            "assigned_unit_id": req.assigned_unit_id,
        })
        _require_ratified(gate, wazuh, "arms-register", req.tenant_state_id)
        unit = store.units.get(req.assigned_unit_id)
        if unit is None:
            raise HTTPException(status_code=404,
                                detail=f"unit '{req.assigned_unit_id}' not found")
        entry = {
            "entry_id": f"arm-{req.serial_no}",
            "tenant_state_id": req.tenant_state_id,
            "serial_no": req.serial_no,
            "weapon_type": req.weapon_type,
            "assigned_unit_id": req.assigned_unit_id,
        }
        return entry

    @app.post("/cad/v1/state-force/stand-up", status_code=status.HTTP_201_CREATED,
              tags=["ratification_gated"])
    def stand_up(req: StandUpRequest, gate: RatificationGate = Depends(get_gate),
                 wazuh: WazuhAdapter = Depends(get_wazuh)):
        """State-force stand-up order. GATED until ratification + assent + NASS
        certification against national standards."""
        _require_ratified(gate, wazuh, "state-force-stand-up", req.tenant_state_id)
        return {
            "order_id": f"sf-{req.tenant_state_id}-{req.force_name.lower().replace(' ', '-')}",
            "tenant_state_id": req.tenant_state_id,
            "force_name": req.force_name,
            "enabling_law_ref": req.enabling_law_ref,
            "initial_strength": req.initial_strength,
            "status": "pending_nass_certification",
        }

    @app.get("/cad/v1/gate")
    def gate_status(gate: RatificationGate = Depends(get_gate)):
        return {"ratified": gate.ratified,
                "gated_categories": ["arms-register", "state-force-stand-up"],
                "legal_basis": gate.legal_basis}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # --- /metrics with dispatch-latency SLO percentiles -----------------------
    # Registered BEFORE instrument_fastapi so this handler wins route matching
    # and can append the CAD dispatch-latency series to the shared exposition.
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
        base = reg.render_prometheus("mod-police-cad") if reg is not None else ""
        slo = request.app.state.store.latency.summary()
        extra = (
            "# HELP cad_dispatch_latency_seconds Dispatch assignment latency "
            "percentiles (incident-created -> dispatch-assigned).\n"
            "# TYPE cad_dispatch_latency_seconds gauge\n"
            f"cad_dispatch_latency_p50_seconds {slo['p50_seconds']}\n"
            f"cad_dispatch_latency_p95_seconds {slo['p95_seconds']}\n"
            "# HELP cad_dispatch_slo_breaches_total Dispatches exceeding the "
            "30 s dispatch SLO.\n"
            "# TYPE cad_dispatch_slo_breaches_total counter\n"
            f"cad_dispatch_slo_breaches_total {slo['breach_count']}\n"
            "# HELP cad_dispatch_samples_total Recorded dispatch latency samples.\n"
            "# TYPE cad_dispatch_samples_total counter\n"
            f"cad_dispatch_samples_total {slo['sample_count']}\n"
        )
        return PlainTextResponse(base + extra)

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-police-cad", registry=registry)
    return app


app = create_app()
