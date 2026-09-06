"""mod-gis-lands FastAPI application — implements contracts/openapi/cadastre-parcels.yaml.

Endpoints (all tenant-scoped by ``state_id`` path parameter, matching the
contract's ``StateId`` parameter enum):

* ``POST /api/v1/states/{state_id}/cadastre/parcels``      — registerParcel (201/409)
* ``GET  /api/v1/states/{state_id}/cadastre/parcels``      — searchParcels (lga_id, within WKT)
* ``POST /api/v1/states/{state_id}/cadastre/deeds/verify`` — verifyDeed
* ``GET  /api/v1/states/{state_id}/cadastre/parcels/{parcel_id}``        — internal: parcel fetch
* ``GET  /api/v1/states/{state_id}/cadastre/titling/{workflow_id}``      — internal: workflow + SLA status
* ``POST /api/v1/states/{state_id}/cadastre/titling/{workflow_id}/decisions`` — internal: approval signal

AuthN/Z: the contract requires ``bearerAuth`` JWT (Keycloak realm per state —
ADR-006). Token validation happens at the APISIX gateway (ADR-007); this
service trusts upstream-authenticated requests in local/test mode and never
mixes tenants because every repository call is explicitly state-scoped.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel

from .geometry import (
    GeometryError,
    OverlapError,
    check_declared_area,
    find_overlap,
    parse_boundary,
)
from .repository import (
    DuplicateParcelError,
    InMemoryParcelRepository,
    ParcelRepository,
    parse_within_wkt,
)
from .schemas import (
    DeedVerificationRequest,
    DeedVerificationResult,
    Parcel,
    ParcelRecord,
    ParcelRegistration,
    StateId,
)
from .signing import SignatureError, verify_payload
from .titling import (
    LocalTitlingRunner,
    TitlingWorkflowInstance,
    WorkflowError,
    WorkflowStatus,
    next_pending_stage,
)


class TitlingDecisionIn(BaseModel):
    """Approval-signal body for the internal titling endpoint."""

    approved: bool
    actor: str
    note: str = ""


class TitlingStatusOut(BaseModel):
    """Internal workflow status projection, incl. SLA evaluation."""

    workflow_id: str
    parcel_id: UUID
    stage: str
    status: str
    pending_stage: Optional[str]
    c_of_o_number: Optional[str]
    signed_title_jws: Optional[str]
    rejection_reason: Optional[str]
    sla: dict


def _to_contract_parcel(record: ParcelRecord) -> Parcel:
    """Project the internal row to the contract's Parcel response schema."""
    return Parcel(
        parcel_id=record.parcel_id,
        parcel_uin=record.parcel_uin,
        title_type=record.title_type,
        c_of_o_number=record.c_of_o_number,
        status=record.status.value,
        titling_workflow_id=record.titling_workflow_id,
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


def create_app(
    repository: ParcelRepository | None = None,
    titling_runner: LocalTitlingRunner | None = None,
) -> FastAPI:
    """Application factory — inject repository/runner for tests."""

    app = FastAPI(title="SOS Cadastral Land Administration API", version="1.0.0")
    repo: ParcelRepository = repository or InMemoryParcelRepository()
    runner = titling_runner or LocalTitlingRunner()

    def get_repo() -> ParcelRepository:
        return repo

    def get_runner() -> LocalTitlingRunner:
        return runner

    # -- registerParcel ------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels",
        status_code=status.HTTP_201_CREATED,
        response_model=Parcel,
    )
    def register_parcel(
        state_id: StateId, body: ParcelRegistration, response: Response,
        repo: ParcelRepository = Depends(get_repo),
        runner: LocalTitlingRunner = Depends(get_runner),
    ) -> Parcel:
        try:
            geom = parse_boundary(body.boundary_geojson)
            check_declared_area(geom, body.area_sqm)
        except GeometryError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

        # Topological overlap check — service-level mirror of
        # trg_parcels_no_overlap (0001_cadastre.sql), tenant-scoped by RLS
        # semantics: only ACTIVE parcels of THIS state can conflict.
        conflict = find_overlap(geom, repo.active_geometries(state_id.value))
        if conflict is not None:
            assert isinstance(conflict, OverlapError)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Boundary overlaps gazetted reserve, setback, or existing title: {conflict}",
            )

        record = ParcelRecord(
            parcel_id=uuid4(),
            tenant_state_id=state_id.value,
            lga_id=body.lga_id,
            parcel_uin=body.parcel_uin,
            owner_stin=body.owner_stin,
            land_use_type=body.land_use_type,
            survey_plan_no=body.survey_plan_no,
            beacon_count=body.beacon_count,
            area_sqm=body.area_sqm,
            boundary_geojson=body.boundary_geojson,
            titling_workflow_id="",  # set below, after workflow start
        )
        try:
            repo.add(record)
        except DuplicateParcelError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

        # e-C-of-O Temporal workflow initiated (local runner in tests).
        instance = runner.start(record)
        record = record.model_copy(update={"titling_workflow_id": instance.workflow_id})
        repo.update(record)
        return _to_contract_parcel(record)

    # -- searchParcels -------------------------------------------------------
    @app.get("/api/v1/states/{state_id}/cadastre/parcels", response_model=list[Parcel])
    def search_parcels(
        state_id: StateId,
        lga_id: Optional[str] = None,
        within: Optional[str] = Query(default=None, description="WKT polygon (EPSG:4326)"),
        repo: ParcelRepository = Depends(get_repo),
    ) -> list[Parcel]:
        within_geom = None
        if within is not None:
            try:
                within_geom = parse_within_wkt(within)
            except GeometryError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        return [_to_contract_parcel(r) for r in repo.search(state_id.value, lga_id, within_geom)]

    # -- internal: fetch one parcel -------------------------------------------
    @app.get("/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}", response_model=Parcel)
    def get_parcel(
        state_id: StateId, parcel_id: UUID, repo: ParcelRepository = Depends(get_repo)
    ) -> Parcel:
        record = repo.get(state_id.value, parcel_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "parcel not found")
        return _to_contract_parcel(record)

    # -- verifyDeed -----------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/deeds/verify",
        response_model=DeedVerificationResult,
    )
    def verify_deed(
        state_id: StateId,
        body: DeedVerificationRequest,
        repo: ParcelRepository = Depends(get_repo),
        runner: LocalTitlingRunner = Depends(get_runner),
    ) -> DeedVerificationResult:
        instance = runner.find_by_c_of_o(state_id.value, body.c_of_o_number)
        base = dict(c_of_o_number=body.c_of_o_number, tenant_state_id=state_id.value)
        if instance is None:
            return DeedVerificationResult(valid=False, detail="no issued title with this C-of-O number in this state", **base)

        record = repo.get(state_id.value, instance.parcel_id)
        parcel_uin = record.parcel_uin if record else None
        if body.parcel_uin is not None and parcel_uin != body.parcel_uin:
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin,
                detail="C-of-O number does not match the supplied parcel_uin", **base,
            )

        # Verify the full signature chain: registry issuance + governor consent.
        try:
            verify_payload(instance.signed_title_jws or "", runner.registry_public_key)
            verify_payload(instance.governor_consent_jws or "", runner.governor_public_key)
        except SignatureError as exc:
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin, detail=f"signature chain broken: {exc}", **base,
            )

        return DeedVerificationResult(
            valid=True,
            parcel_uin=parcel_uin,
            title_type=record.title_type if record else None,
            signature_chain=[instance.signed_title_jws or "", instance.governor_consent_jws or ""],
            detail="title authentic: registry signature and governor consent verified",
            **base,
        )

    # -- internal: titling workflow signals & status ---------------------------
    def _resolve(state_id: StateId, workflow_id: str, repo, runner):
        instance = runner.get(workflow_id)
        if instance.tenant_state_id != state_id.value:
            # Tenancy isolation: workflows are invisible across state tenants.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
        record = repo.get(state_id.value, instance.parcel_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "parcel not found")
        return instance, record

    @app.get(
        "/api/v1/states/{state_id}/cadastre/titling/{workflow_id}",
        response_model=TitlingStatusOut,
    )
    def titling_status(
        state_id: StateId, workflow_id: str,
        repo: ParcelRepository = Depends(get_repo),
        runner: LocalTitlingRunner = Depends(get_runner),
    ) -> TitlingStatusOut:
        try:
            instance, _ = _resolve(state_id, workflow_id, repo, runner)
        except WorkflowError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        sla = instance.sla_evaluation(now=runner.now())
        return TitlingStatusOut(
            workflow_id=instance.workflow_id,
            parcel_id=instance.parcel_id,
            stage=instance.stage.value,
            status=instance.status.value,
            pending_stage=(s.value if (s := next_pending_stage(instance)) else None),
            c_of_o_number=instance.c_of_o_number,
            signed_title_jws=instance.signed_title_jws,
            rejection_reason=instance.rejection_reason,
            sla={
                "tenant_state_id": sla.tenant_state_id,
                "elapsed_days": round(sla.elapsed_days, 4),
                "total_days_allowed": sla.total_days_allowed,
                "total_breached": sla.total_breached,
                "warn_threshold_reached": sla.warn_threshold_reached,
                "stage_breaches": [b.__dict__ for b in sla.stage_breaches],
            },
        )

    @app.post(
        "/api/v1/states/{state_id}/cadastre/titling/{workflow_id}/decisions",
        response_model=TitlingStatusOut,
    )
    def titling_decide(
        state_id: StateId, workflow_id: str, body: TitlingDecisionIn,
        repo: ParcelRepository = Depends(get_repo),
        runner: LocalTitlingRunner = Depends(get_runner),
    ) -> TitlingStatusOut:
        try:
            instance, record = _resolve(state_id, workflow_id, repo, runner)
            runner.advance(
                workflow_id, record, approved=body.approved, actor=body.actor, note=body.note
            )
            # Reflect terminal workflow state (issued title / rejection) on the
            # parcel registry row — the issuance activity's PostGIS UPDATE.
            repo.update(runner.apply_issuance_to_parcel(record))
        except WorkflowError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        return titling_status(state_id, workflow_id, repo, runner)

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-gis-lands")
    return app


#: Module-level app for ``uvicorn lands_app.main:app`` local runs.
app = create_app()
