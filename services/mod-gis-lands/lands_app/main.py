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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel

from .anchoring import AnchorAdapter, anchor_adapter_from_env
from .disputes import (
    DisputeActiveError,
    DisputeError,
    DisputeGrounds,
    DisputeGuard,
    DisputeStore,
    DuplicateDisputeError,
    IllegalTransitionError,
)
from .eventlog import CadastreEventLog
from .history import chain_of_title
from .risk import RiskFactor, TitleRiskAdapter, risk_scorer_from_env
from .subdivision import (
    AREA_CONSERVATION_TOLERANCE,
    AreaConservationError,
    ChildParcelSpec,
    SubdivisionError,
    SubdivisionService,
)

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


class SubdivideIn(BaseModel):
    """Subdivision request: the child parcels tiling the parent."""

    children: list[ChildParcelSpec]
    actor: str = "lands-registry"


class MergeIn(BaseModel):
    """Merger request: 2+ adjacent parents folded into one child parcel."""

    parent_parcel_ids: list[UUID]
    child: ChildParcelSpec
    actor: str = "lands-registry"


class DisputeIn(BaseModel):
    """Dispute lodgement body."""

    complainant: str
    grounds: DisputeGrounds
    description: str = ""


class DisputeDecisionIn(BaseModel):
    """Dispute review/resolve/dismiss body."""

    actor: str
    resolution_note: str = ""


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


# --- Prometheus operation counters (optional dep; no-op fallback) ----------
try:
    from prometheus_client import Counter as _Counter

    _OPS_COUNTER = _Counter(
        "lands_cadastre_operations_total",
        "Cadastre operations by type and outcome",
        ["operation", "outcome"],
    )
except Exception:  # pragma: no cover - prometheus-client not installed
    _OPS_COUNTER = None


def _count(operation: str, outcome: str = "success") -> None:
    if _OPS_COUNTER is not None:
        _OPS_COUNTER.labels(operation=operation, outcome=outcome).inc()


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
    event_log: CadastreEventLog | None = None,
    dispute_store: DisputeStore | None = None,
    risk_scorer: TitleRiskAdapter | None = None,
    anchor_adapter: AnchorAdapter | None = None,
) -> FastAPI:
    """Application factory — inject seams for tests.

    Production fail-closed boot: when ``SOS_LANDS_PROFILE=production`` and the
    risk/anchor seams are not injected *and* their env URLs are unset,
    ``*_from_env`` raises ``AdapterUnavailableError`` here.
    """

    app = FastAPI(title="SOS Cadastral Land Administration API", version="1.0.0")
    repo: ParcelRepository = repository or InMemoryParcelRepository()
    runner = titling_runner or LocalTitlingRunner()
    events = event_log or CadastreEventLog()
    disputes = dispute_store or DisputeStore(event_log=events)
    guard = DisputeGuard(disputes)
    risk = risk_scorer if risk_scorer is not None else risk_scorer_from_env()
    anchors = anchor_adapter if anchor_adapter is not None else anchor_adapter_from_env()
    subdivisions = SubdivisionService(repo, runner, events, guard)

    def get_repo() -> ParcelRepository:
        return repo

    def get_runner() -> LocalTitlingRunner:
        return runner

    def tenant_from_header(
        state_id: StateId, x_state_tenant: str | None = Header(default=None)
    ) -> str:
        """Tenant guard for the extension endpoints (house idiom).

        The ``X-State-Tenant`` header is required and must match the path
        ``state_id`` — belt-and-braces over the path-scoped repository calls.
        """
        if not x_state_tenant:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-State-Tenant header is required")
        if x_state_tenant.lower() != state_id.value:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "X-State-Tenant header does not match the state_id path parameter",
            )
        return state_id.value

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
        events.record(
            "PARCEL_REGISTERED",
            state_id.value,
            parcel_id=record.parcel_id,
            detail={"parcel_uin": record.parcel_uin, "owner_stin": record.owner_stin},
        )
        _count("register_parcel")
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
            # DisputeGuard: an open/under-review dispute freezes titling approvals.
            guard.assert_clear(state_id.value, record.parcel_id)
            runner.advance(
                workflow_id, record, approved=body.approved, actor=body.actor, note=body.note
            )
            # Reflect terminal workflow state (issued title / rejection) on the
            # parcel registry row — the issuance activity's PostGIS UPDATE.
            repo.update(runner.apply_issuance_to_parcel(record))
        except (WorkflowError, DisputeActiveError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        return titling_status(state_id, workflow_id, repo, runner)

    # =========================================================================
    # Extension endpoints (subdivision / merger / disputes / history / risk /
    # anchoring). All are tenant-scoped: path state_id + X-State-Tenant header.
    # =========================================================================

    def _get_parcel_or_404(state_id: StateId, parcel_id: UUID) -> ParcelRecord:
        record = repo.get(state_id.value, parcel_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "parcel not found")
        return record

    # -- subdivision ---------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/subdivide",
        status_code=status.HTTP_201_CREATED,
    )
    def subdivide_parcel(
        state_id: StateId, parcel_id: UUID, body: SubdivideIn,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        _get_parcel_or_404(state_id, parcel_id)
        try:
            parent, children = subdivisions.subdivide(
                tenant, parcel_id, body.children, actor=body.actor
            )
        except AreaConservationError as exc:
            _count("subdivide", "rejected")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        except (SubdivisionError, DisputeActiveError, DuplicateParcelError) as exc:
            _count("subdivide", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("subdivide")
        return {
            "parent": {**_to_contract_parcel(parent).model_dump(mode="json")},
            "children": [
                {
                    **_to_contract_parcel(c).model_dump(mode="json"),
                    "parent_parcel_ids": [str(p) for p in c.parent_parcel_ids],
                    "area_sqm": c.area_sqm,
                }
                for c in children
            ],
            "area_conservation_tolerance": AREA_CONSERVATION_TOLERANCE,
        }

    # -- merger ----------------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/merge",
        status_code=status.HTTP_201_CREATED,
    )
    def merge_parcels(
        state_id: StateId, body: MergeIn,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        for pid in body.parent_parcel_ids:
            _get_parcel_or_404(state_id, pid)
        try:
            parents, child = subdivisions.merge(
                tenant, body.parent_parcel_ids, body.child, actor=body.actor
            )
        except AreaConservationError as exc:
            _count("merge", "rejected")
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        except (SubdivisionError, DisputeActiveError, DuplicateParcelError) as exc:
            _count("merge", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("merge")
        return {
            "parents": [_to_contract_parcel(p).model_dump(mode="json") for p in parents],
            "child": {
                **_to_contract_parcel(child).model_dump(mode="json"),
                "parent_parcel_ids": [str(p) for p in child.parent_parcel_ids],
                "area_sqm": child.area_sqm,
            },
            "area_conservation_tolerance": AREA_CONSERVATION_TOLERANCE,
        }

    # -- chain-of-title history --------------------------------------------------
    @app.get("/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/history")
    def parcel_history(
        state_id: StateId, parcel_id: UUID,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        entries = chain_of_title(tenant, parcel_id, repo, runner, events)
        if entries is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "parcel not found")
        _count("history")
        return {
            "parcel_id": str(parcel_id),
            "tenant_state_id": tenant,
            "chain_of_title": [e.as_dict() for e in entries],
        }

    # -- disputes ------------------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/disputes",
        status_code=status.HTTP_201_CREATED,
    )
    def open_dispute(
        state_id: StateId, parcel_id: UUID, body: DisputeIn,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        _get_parcel_or_404(state_id, parcel_id)
        try:
            dispute = disputes.open(
                tenant, parcel_id,
                complainant=body.complainant, grounds=body.grounds,
                description=body.description,
            )
        except DuplicateDisputeError as exc:
            _count("dispute_open", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("dispute_open")
        return dispute.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/disputes")
    def list_disputes(
        state_id: StateId, parcel_id: UUID,
        tenant: str = Depends(tenant_from_header),
    ) -> list[dict]:
        _get_parcel_or_404(state_id, parcel_id)
        _count("dispute_list")
        return [d.as_dict() for d in disputes.list_for_parcel(tenant, parcel_id)]

    @app.post("/api/v1/states/{state_id}/cadastre/disputes/{dispute_id}/review")
    def review_dispute(
        state_id: StateId, dispute_id: str, body: DisputeDecisionIn,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            dispute = disputes.review(tenant, dispute_id, actor=body.actor)
        except DisputeError as exc:
            code = status.HTTP_409_CONFLICT if isinstance(exc, IllegalTransitionError) else status.HTTP_404_NOT_FOUND
            _count("dispute_review", "conflict")
            raise HTTPException(code, str(exc))
        _count("dispute_review")
        return dispute.as_dict()

    @app.post("/api/v1/states/{state_id}/cadastre/disputes/{dispute_id}/resolve")
    def resolve_dispute(
        state_id: StateId, dispute_id: str, body: DisputeDecisionIn,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        if not body.resolution_note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "resolution_note is required")
        try:
            dispute = disputes.resolve(
                tenant, dispute_id, resolver=body.actor, resolution_note=body.resolution_note
            )
        except DisputeError as exc:
            code = status.HTTP_409_CONFLICT if isinstance(exc, IllegalTransitionError) else status.HTTP_404_NOT_FOUND
            _count("dispute_resolve", "conflict")
            raise HTTPException(code, str(exc))
        _count("dispute_resolve")
        return dispute.as_dict()

    @app.post("/api/v1/states/{state_id}/cadastre/disputes/{dispute_id}/dismiss")
    def dismiss_dispute(
        state_id: StateId, dispute_id: str, body: DisputeDecisionIn,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            dispute = disputes.dismiss(
                tenant, dispute_id, resolver=body.actor, resolution_note=body.resolution_note
            )
        except DisputeError as exc:
            code = status.HTTP_409_CONFLICT if isinstance(exc, IllegalTransitionError) else status.HTTP_404_NOT_FOUND
            _count("dispute_dismiss", "conflict")
            raise HTTPException(code, str(exc))
        _count("dispute_dismiss")
        return dispute.as_dict()

    # -- title risk scoring ----------------------------------------------------
    @app.get("/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/risk")
    def parcel_risk(
        state_id: StateId, parcel_id: UUID,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        record = _get_parcel_or_404(state_id, parcel_id)
        factors: list[RiskFactor] = []
        if disputes.has_open(tenant, parcel_id):
            factors.append(RiskFactor(
                kind="OPEN_DISPUTE",
                detail="parcel has an open/under-review dispute",
                weight=30,
            ))
        lineage_events = [
            e for e in events.events(tenant, parcel_id)
            if e.event_type in ("PARCEL_SUBDIVIDED", "PARCEL_MERGED",
                                "PARCEL_CREATED_FROM_SUBDIVISION")
        ]
        if len(lineage_events) >= 2:
            factors.append(RiskFactor(
                kind="RAPID_SUCCESSIVE_TRANSFERS",
                detail=f"{len(lineage_events)} lineage mutations recorded",
                weight=15,
            ))
        if record.parent_parcel_ids and any(
            repo.get(tenant, p) is None for p in record.parent_parcel_ids
        ):
            factors.append(RiskFactor(
                kind="SUPERSEDED_LINEAGE_GAP",
                detail="lineage references a parent parcel missing from the registry",
                weight=20,
            ))
        assessment = risk.score(
            tenant_state_id=tenant, parcel_id=str(parcel_id), factors=factors
        )
        _count("risk_score")
        return assessment.as_dict()

    # -- title-hash anchoring ----------------------------------------------------
    def _title_payload(record: ParcelRecord) -> dict:
        return {
            "tenant_state_id": record.tenant_state_id,
            "parcel_id": str(record.parcel_id),
            "parcel_uin": record.parcel_uin,
            "owner_stin": record.owner_stin,
            "title_type": record.title_type.value,
            "c_of_o_number": record.c_of_o_number,
            "area_sqm": record.area_sqm,
            "survey_plan_no": record.survey_plan_no,
            "status": record.status.value,
        }

    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/anchor",
        status_code=status.HTTP_201_CREATED,
    )
    def anchor_title(
        state_id: StateId, parcel_id: UUID,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        record = _get_parcel_or_404(state_id, parcel_id)
        anchor = anchors.anchor(
            tenant_state_id=tenant, parcel_id=str(parcel_id), payload=_title_payload(record)
        )
        events.record(
            "TITLE_ANCHORED",
            tenant,
            parcel_id=parcel_id,
            detail={"anchor_id": anchor.anchor_id, "anchor_hash": anchor.anchor_hash},
        )
        _count("anchor")
        return anchor.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/anchors/{anchor_id}/verify")
    def verify_anchor(
        state_id: StateId, anchor_id: str,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        anchor = anchors.get(tenant, anchor_id)
        if anchor is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "anchor not found")
        record = repo.get(tenant, UUID(anchor.parcel_id))
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "anchored parcel not found")
        valid = anchors.verify(
            tenant_state_id=tenant, anchor_id=anchor_id, payload=_title_payload(record)
        )
        _count("anchor_verify", "valid" if valid else "tampered")
        return {
            "anchor_id": anchor_id,
            "tenant_state_id": tenant,
            "valid": valid,
            "merkle_root": anchor.merkle_root,
            "detail": "anchor intact" if valid else "payload or anchor-chain mismatch — tamper detected",
        }

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-gis-lands")
    return app


#: Module-level app for ``uvicorn lands_app.main:app`` local runs.
app = create_app()
