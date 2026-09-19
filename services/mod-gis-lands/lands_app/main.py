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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from datetime import datetime

from .anchoring import AnchorAdapter, anchor_adapter_from_env
from .court_orders import CourtOrderError, CourtOrderService, CourtOrderType
from .encumbrances import (
    EncumbranceActiveError,
    EncumbranceError,
    EncumbranceGuard,
    EncumbranceNotFoundError,
    EncumbranceStore,
    EncumbranceType,
    IllegalEncumbranceTransitionError,
)
from .legal_adapters import (
    LandDocsAdapter,
    TaxClearanceAdapter,
    docs_adapter_from_env,
    tax_adapter_from_env,
)
from .revocation import (
    CompensationCategory,
    CompensationLineItem,
    LandCompensationLedgerAdapter,
    RevocationError,
    RevocationService,
    ledger_adapter_from_env,
)
from .succession import TransmissionError, TransmissionService
from .transfers import (
    TitleRegistry,
    TitleStatus,
    TransferError,
    TransferInstrumentType,
    TransferNotFoundError,
    TransferService,
)
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
from .risk import AdapterUnavailableError, RiskFactor, TitleRiskAdapter, risk_scorer_from_env
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
    ParcelStatus,
    StateId,
)
from .signing import SignatureError, verify_payload
from .titling import (
    LocalTitlingRunner,
    STAGE_ROLES,
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


# -- legal/conveyancing layer request bodies ---------------------------------


class EncumbranceIn(BaseModel):
    """Encumbrance registration body."""

    type: EncumbranceType
    instrument_hash: str
    priority: int = 0
    expires_at: Optional[datetime] = None
    actor: str = "lands-registry"


class EncumbranceCloseIn(BaseModel):
    """Encumbrance release/withdrawal body."""

    actor: str
    reason: str = ""


class TransferIn(BaseModel):
    """Transfer application body."""

    instrument_type: TransferInstrumentType
    transferor_stin: str
    transferee_stin: str
    evidence_document_id: str
    actor: str = "lands-registry"


class TransferAdvanceIn(BaseModel):
    """Transfer stage-advance body."""

    actor: str
    consent_id: Optional[str] = None
    note: str = ""


class ConsentIn(BaseModel):
    """Governor consent instrument issuance body."""

    parcel_id: UUID
    expires_at: datetime
    actor: str = "governor"


class TransmissionIn(BaseModel):
    """Transmission (probate) death-report body."""

    deceased_stin: str
    death_certificate_doc_id: str
    beneficiaries: list[str]
    actor: str = "lands-registry"


class TransmissionAdvanceIn(BaseModel):
    actor: str
    note: str = ""


class CourtOrderIn(BaseModel):
    """Court-order filing body."""

    parcel_id: UUID
    order_type: CourtOrderType
    order_number: str
    court: str
    instrument_hash: str
    effective_date: str
    new_owner_stin: Optional[str] = None
    new_boundary_geojson: Optional[dict] = None
    actor: str = "registrar:filings"


class CourtOrderApproveIn(BaseModel):
    actor: str
    role: str


class CourtOrderApplyIn(BaseModel):
    actor: str = "registrar:filings"


class RevocationIn(BaseModel):
    """Revocation notice body."""

    public_purpose: str
    actor: str = "ministry:lands"


class CompensationItemIn(BaseModel):
    category: CompensationCategory
    amount_kobo: int = Field(ge=0)


class RevocationAdvanceIn(BaseModel):
    actor: str
    line_items: Optional[list[CompensationItemIn]] = None
    valuer: Optional[str] = None
    override_reason: Optional[str] = None
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

# --- Shared OIDC JWT authorization (services/_shared/auth.py) ---------------
try:
    from _shared.auth import assert_auth_bootable as _assert_auth_bootable
    from _shared.auth import require_role as _require_role
except ImportError:  # minimal container images ship only the app package
    import sys as _sys2
    from pathlib import Path as _Path2

    _sr = _Path2(__file__).resolve().parents[2]
    if str(_sr) not in _sys2.path:
        _sys2.path.insert(0, str(_sr))
    try:
        from _shared.auth import assert_auth_bootable as _assert_auth_bootable
        from _shared.auth import require_role as _require_role
    except ImportError:
        def _assert_auth_bootable() -> None:  # type: ignore[misc]
            return None

        def _require_role(role: str):  # type: ignore[misc]
            from fastapi import Request

            def _dep(request: Request) -> str:
                return f"{role}:anonymous"

            return _dep


def create_app(
    repository: ParcelRepository | None = None,
    titling_runner: LocalTitlingRunner | None = None,
    event_log: CadastreEventLog | None = None,
    dispute_store: DisputeStore | None = None,
    risk_scorer: TitleRiskAdapter | None = None,
    anchor_adapter: AnchorAdapter | None = None,
    encumbrance_store: EncumbranceStore | None = None,
    docs_adapter: LandDocsAdapter | None = None,
    tax_adapter: TaxClearanceAdapter | None = None,
    ledger_adapter: LandCompensationLedgerAdapter | None = None,
) -> FastAPI:
    """Application factory — inject seams for tests.

    Production fail-closed boot: when ``SOS_LANDS_PROFILE=production`` and the
    risk/anchor/docs/tax/ledger seams are not injected *and* their env URLs are
    unset, ``*_from_env`` raises ``AdapterUnavailableError`` here. With
    ``SOS_AUTH_PROFILE=production`` a missing ``SOS_AUTH_JWKS_URL`` is a boot
    error (fail-closed OIDC enforcement, services/_shared/auth.py).
    """

    _assert_auth_bootable()
    app = FastAPI(title="SOS Cadastral Land Administration API", version="1.0.0")
    repo: ParcelRepository = repository or InMemoryParcelRepository()
    runner = titling_runner or LocalTitlingRunner()
    events = event_log or CadastreEventLog()
    disputes = dispute_store or DisputeStore(event_log=events)
    guard = DisputeGuard(disputes)
    risk = risk_scorer if risk_scorer is not None else risk_scorer_from_env()
    anchors = anchor_adapter if anchor_adapter is not None else anchor_adapter_from_env()

    # -- legal/conveyancing layer seams -------------------------------------
    encumbrances = encumbrance_store or EncumbranceStore(event_log=events)
    eguard = EncumbranceGuard(encumbrances)
    docs = docs_adapter if docs_adapter is not None else docs_adapter_from_env()
    tax = tax_adapter if tax_adapter is not None else tax_adapter_from_env()
    ledger = ledger_adapter if ledger_adapter is not None else ledger_adapter_from_env()
    titles = TitleRegistry()

    def previous_title_hash(tenant: str, parcel_id: UUID) -> str:
        """Hash the signed transfer/revocation instruments chain to."""
        import hashlib as _hashlib

        current = titles.current_for(tenant, parcel_id)
        if current is not None and current.title_jws:
            return _hashlib.sha256(current.title_jws.encode("ascii")).hexdigest()
        record = repo.get(tenant, parcel_id)
        if record is not None and record.titling_workflow_id:
            try:
                instance = runner.get(record.titling_workflow_id)
            except WorkflowError:
                instance = None
            if instance is not None and instance.signed_title_jws:
                return _hashlib.sha256(
                    instance.signed_title_jws.encode("ascii")
                ).hexdigest()
        return _hashlib.sha256(
            f"genesis|{tenant}|{parcel_id}".encode("utf-8")
        ).hexdigest()

    transfers = TransferService(
        repo, events, eguard, guard, docs, tax, titles,
        registry_key=runner.registry_private_key,
        sequence=runner.c_of_o_sequence,
        previous_title_hash=previous_title_hash,
    )
    transmissions = TransmissionService(repo, events, docs, transfers)
    court_orders = CourtOrderService(repo, events, transfers)
    revocations = RevocationService(
        repo, events, ledger, titles,
        governor_key=runner.governor_private_key,
        previous_title_hash=previous_title_hash,
    )
    subdivisions = SubdivisionService(repo, runner, events, guard, eguard)

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
        title_rec = titles.find(state_id.value, body.c_of_o_number)
        base = dict(c_of_o_number=body.c_of_o_number, tenant_state_id=state_id.value)
        if instance is None and title_rec is None:
            return DeedVerificationResult(valid=False, detail="no issued title with this C-of-O number in this state", **base)

        parcel_id = title_rec.parcel_id if title_rec is not None else instance.parcel_id
        record = repo.get(state_id.value, parcel_id)
        parcel_uin = record.parcel_uin if record else None
        if body.parcel_uin is not None and parcel_uin != body.parcel_uin:
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin,
                parcel_status=record.status.value if record else None,
                detail="C-of-O number does not match the supplied parcel_uin", **base,
            )

        # Lifecycle-aware: a replaced or revoked title is never valid, even if
        # its signatures are intact.
        if title_rec is not None and title_rec.status == TitleStatus.REPLACED:
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin,
                parcel_status=record.status.value if record else None,
                reference=title_rec.replaced_by,
                detail=f"title has been REPLACED by {title_rec.replaced_by}; "
                "verify the replacement C-of-O instead",
                **base,
            )
        if title_rec is not None and title_rec.status == TitleStatus.REVOKED:
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin,
                parcel_status=record.status.value if record else None,
                reference=title_rec.revocation_reference,
                detail=f"title REVOKED (reference {title_rec.revocation_reference})",
                **base,
            )
        # A SUPERSEDED/REVOKED/ARCHIVED parcel invalidates the deed even when
        # the title register has no replacement entry (e.g. subdivision).
        if record is not None and record.status not in (
            ParcelStatus.ACTIVE, ParcelStatus.REGISTERED
        ):
            reference = None
            if title_rec is not None:
                reference = title_rec.replaced_by or title_rec.revocation_reference
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin,
                parcel_status=record.status.value, reference=reference,
                detail=f"parcel status is {record.status.value}; the deed is no "
                "longer a current title", **base,
            )

        # Verify the signature chain: titling titles carry registry issuance +
        # governor consent; transfer titles carry the signed transfer JWS.
        try:
            if title_rec is not None and title_rec.title_jws:
                verify_payload(title_rec.title_jws, runner.registry_public_key)
                chain = [title_rec.title_jws]
            else:
                assert instance is not None
                verify_payload(instance.signed_title_jws or "", runner.registry_public_key)
                verify_payload(instance.governor_consent_jws or "", runner.governor_public_key)
                chain = [instance.signed_title_jws or "", instance.governor_consent_jws or ""]
        except SignatureError as exc:
            return DeedVerificationResult(
                valid=False, parcel_uin=parcel_uin,
                parcel_status=record.status.value if record else None,
                detail=f"signature chain broken: {exc}", **base,
            )

        return DeedVerificationResult(
            valid=True,
            parcel_uin=parcel_uin,
            title_type=record.title_type if record else None,
            parcel_status=record.status.value if record else None,
            signature_chain=chain,
            detail="title authentic: signature chain verified and title is current",
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
        request: Request, response: Response,
        repo: ParcelRepository = Depends(get_repo),
        runner: LocalTitlingRunner = Depends(get_runner),
    ) -> TitlingStatusOut:
        try:
            instance, record = _resolve(state_id, workflow_id, repo, runner)
            # OIDC role gate (fail-closed in production, dev passthrough):
            # the caller must hold the role mapped to the pending stage.
            pending = next_pending_stage(instance)
            stage_role = STAGE_ROLES.get(pending, "registry")
            _require_role(stage_role)(request, response)
            # DisputeGuard: an open/under-review dispute freezes titling approvals.
            guard.assert_clear(state_id.value, record.parcel_id)
            # EncumbranceGuard: active encumbrances freeze titling approvals.
            eguard.assert_clear(state_id.value, record.parcel_id)
            runner.advance(
                workflow_id, record, approved=body.approved, actor=body.actor, note=body.note
            )
            # Reflect terminal workflow state (issued title / rejection) on the
            # parcel registry row — the issuance activity's PostGIS UPDATE.
            repo.update(runner.apply_issuance_to_parcel(record))
        except (WorkflowError, DisputeActiveError, EncumbranceActiveError) as exc:
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
        except (SubdivisionError, DisputeActiveError, EncumbranceActiveError, DuplicateParcelError) as exc:
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
        except (SubdivisionError, DisputeActiveError, EncumbranceActiveError, DuplicateParcelError) as exc:
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
        _actor: str = Depends(_require_role("registry")),
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

    # =========================================================================
    # Legal/conveyancing layer: encumbrances, transfers, consents, probate
    # transmissions, court orders, revocations. All tenant-scoped via the
    # X-State-Tenant header guard.
    # =========================================================================

    # -- encumbrance register ---------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/encumbrances",
        status_code=status.HTTP_201_CREATED,
    )
    def register_encumbrance(
        state_id: StateId, parcel_id: UUID, body: EncumbranceIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        _get_parcel_or_404(state_id, parcel_id)
        enc = encumbrances.register(
            tenant, parcel_id,
            type=body.type, instrument_hash=body.instrument_hash,
            priority=body.priority, expires_at=body.expires_at, actor=body.actor,
        )
        _count("encumbrance_register")
        return enc.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/encumbrances")
    def list_encumbrances(
        state_id: StateId, parcel_id: UUID, include_closed: bool = False,
        tenant: str = Depends(tenant_from_header),
    ) -> list[dict]:
        _get_parcel_or_404(state_id, parcel_id)
        return [
            e.as_dict()
            for e in encumbrances.list_for_parcel(
                tenant, parcel_id, include_closed=include_closed
            )
        ]

    def _close_encumbrance(state_id, encumbrance_id, body, tenant, action):
        try:
            if action == "release":
                enc = encumbrances.release(
                    tenant, encumbrance_id, actor=body.actor, reason=body.reason
                )
            else:
                enc = encumbrances.withdraw(
                    tenant, encumbrance_id, actor=body.actor, reason=body.reason
                )
        except EncumbranceNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except IllegalEncumbranceTransitionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count(f"encumbrance_{action}")
        return enc.as_dict()

    @app.post("/api/v1/states/{state_id}/cadastre/encumbrances/{encumbrance_id}/release")
    def release_encumbrance(
        state_id: StateId, encumbrance_id: str, body: EncumbranceCloseIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        return _close_encumbrance(state_id, encumbrance_id, body, tenant, "release")

    @app.post("/api/v1/states/{state_id}/cadastre/encumbrances/{encumbrance_id}/withdraw")
    def withdraw_encumbrance(
        state_id: StateId, encumbrance_id: str, body: EncumbranceCloseIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        return _close_encumbrance(state_id, encumbrance_id, body, tenant, "withdraw")

    # -- transfers (ownership dealings) -------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/transfers",
        status_code=status.HTTP_201_CREATED,
    )
    def apply_transfer(
        state_id: StateId, parcel_id: UUID, body: TransferIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        _get_parcel_or_404(state_id, parcel_id)
        try:
            application = transfers.apply(
                tenant, parcel_id,
                instrument_type=body.instrument_type,
                transferor_stin=body.transferor_stin,
                transferee_stin=body.transferee_stin,
                evidence_document_id=body.evidence_document_id,
                actor=body.actor,
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except (TransferError, DisputeActiveError, EncumbranceActiveError) as exc:
            _count("transfer_apply", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("transfer_apply")
        return application.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/transfers/{transfer_id}")
    def get_transfer(
        state_id: StateId, transfer_id: str,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            return transfers.get(tenant, transfer_id).as_dict()
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.post("/api/v1/states/{state_id}/cadastre/transfers/{transfer_id}/advance")
    def advance_transfer(
        state_id: StateId, transfer_id: str, body: TransferAdvanceIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        try:
            application = transfers.advance(
                tenant, transfer_id, actor=body.actor,
                consent_id=body.consent_id, note=body.note,
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except (TransferError, DisputeActiveError, EncumbranceActiveError) as exc:
            _count("transfer_advance", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("transfer_advance")
        return application.as_dict()

    # -- governor consent instruments ---------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/consents",
        status_code=status.HTTP_201_CREATED,
    )
    def issue_consent(
        state_id: StateId, body: ConsentIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("governor")),
    ) -> dict:
        _get_parcel_or_404(state_id, body.parcel_id)
        try:
            consent = transfers.issue_consent(
                tenant, body.parcel_id, expires_at=body.expires_at, actor=body.actor
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        _count("consent_issue")
        return consent.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/consents/{consent_id}")
    def get_consent(
        state_id: StateId, consent_id: str,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            return transfers.get_consent(tenant, consent_id).as_dict()
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    # -- probate / transmission ------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/transmissions",
        status_code=status.HTTP_201_CREATED,
    )
    def report_death(
        state_id: StateId, parcel_id: UUID, body: TransmissionIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        _get_parcel_or_404(state_id, parcel_id)
        try:
            case = transmissions.report_death(
                tenant, parcel_id,
                deceased_stin=body.deceased_stin,
                death_certificate_doc_id=body.death_certificate_doc_id,
                beneficiaries=body.beneficiaries,
                actor=body.actor,
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except TransmissionError as exc:
            _count("transmission_report", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("transmission_report")
        return case.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/transmissions/{transmission_id}")
    def get_transmission(
        state_id: StateId, transmission_id: str,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            return transmissions.get(tenant, transmission_id).as_dict()
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.post("/api/v1/states/{state_id}/cadastre/transmissions/{transmission_id}/advance")
    def advance_transmission(
        state_id: StateId, transmission_id: str, body: TransmissionAdvanceIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        try:
            case = transmissions.advance(
                tenant, transmission_id, actor=body.actor, note=body.note
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except (TransmissionError, TransferError, DisputeActiveError,
                EncumbranceActiveError) as exc:
            _count("transmission_advance", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("transmission_advance")
        return case.as_dict()

    # -- court orders ------------------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/court-orders",
        status_code=status.HTTP_201_CREATED,
    )
    def file_court_order(
        state_id: StateId, body: CourtOrderIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        _get_parcel_or_404(state_id, body.parcel_id)
        try:
            order = court_orders.file_order(
                tenant, body.parcel_id,
                order_type=body.order_type, order_number=body.order_number,
                court=body.court, instrument_hash=body.instrument_hash,
                effective_date=body.effective_date, actor=body.actor,
                new_owner_stin=body.new_owner_stin,
                new_boundary_geojson=body.new_boundary_geojson,
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except CourtOrderError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
        _count("court_order_file")
        return order.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/court-orders/{order_id}")
    def get_court_order(
        state_id: StateId, order_id: str,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            return court_orders.get(tenant, order_id).as_dict()
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.post("/api/v1/states/{state_id}/cadastre/court-orders/{order_id}/approve")
    def approve_court_order(
        state_id: StateId, order_id: str, body: CourtOrderApproveIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("ag")),
    ) -> dict:
        try:
            order = court_orders.approve(
                tenant, order_id, actor=body.actor, role=body.role
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except CourtOrderError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("court_order_approve")
        return order.as_dict()

    @app.post("/api/v1/states/{state_id}/cadastre/court-orders/{order_id}/apply")
    def apply_court_order(
        state_id: StateId, order_id: str, body: CourtOrderApplyIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("registry")),
    ) -> dict:
        try:
            order = court_orders.apply(tenant, order_id, actor=body.actor)
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except (CourtOrderError, TransferError, DisputeActiveError,
                EncumbranceActiveError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("court_order_apply")
        return order.as_dict()

    # -- revocation + compensation -------------------------------------------------------
    @app.post(
        "/api/v1/states/{state_id}/cadastre/parcels/{parcel_id}/revocations",
        status_code=status.HTTP_201_CREATED,
    )
    def issue_revocation_notice(
        state_id: StateId, parcel_id: UUID, body: RevocationIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("ministry")),
    ) -> dict:
        _get_parcel_or_404(state_id, parcel_id)
        try:
            case = revocations.issue_notice(
                tenant, parcel_id, public_purpose=body.public_purpose, actor=body.actor
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except RevocationError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("revocation_notice")
        return case.as_dict()

    @app.get("/api/v1/states/{state_id}/cadastre/revocations/{revocation_id}")
    def get_revocation(
        state_id: StateId, revocation_id: str,
        tenant: str = Depends(tenant_from_header),
    ) -> dict:
        try:
            return revocations.get(tenant, revocation_id).as_dict()
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    @app.post("/api/v1/states/{state_id}/cadastre/revocations/{revocation_id}/advance")
    def advance_revocation(
        state_id: StateId, revocation_id: str, body: RevocationAdvanceIn,
        tenant: str = Depends(tenant_from_header),
        _actor: str = Depends(_require_role("ministry")),
    ) -> dict:
        items = None
        if body.line_items is not None:
            items = [
                CompensationLineItem(category=i.category, amount_kobo=i.amount_kobo)
                for i in body.line_items
            ]
        try:
            case = revocations.advance(
                tenant, revocation_id, actor=body.actor,
                line_items=items, valuer=body.valuer,
                override_reason=body.override_reason, note=body.note,
            )
        except TransferNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except AdapterUnavailableError as exc:
            _count("revocation_advance", "unavailable")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
        except RevocationError as exc:
            _count("revocation_advance", "conflict")
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        _count("revocation_advance")
        return case.as_dict()

    if _instrument_fastapi is not None:
        _instrument_fastapi(app, "mod-gis-lands")
    return app


#: Module-level app for ``uvicorn lands_app.main:app`` local runs.
app = create_app()
