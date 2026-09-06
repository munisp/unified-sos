"""mod-kyc-kyb FastAPI application."""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .adapters import (
    CacRegistryAdapter,
    DoclingAdapter,
    FixtureRegistryAdapter,
    LivenessEngine,
    NimcAdapter,
    PaddleOCRAdapter,
    SanctionsAdapter,
    SimulatedDocumentAIAdapter,
    SimulatedVLMAdapter,
    VLMAdapter,
)
from .domain import (
    AuditEntry,
    BusinessType,
    DocumentArtifact,
    DocumentType,
    ExtractionEngine,
    KycCase,
    KybCase,
    LivenessAction,
    LivenessChallenge,
    LivenessEvidence,
    LivenessResult,
    RegistryVerification,
    ReviewDecision,
    ReviewTask,
    SubjectType,
    TenantState,
    utcnow,
)
from .repository import ConflictError, NotFoundError, TenantMismatchError
from .service import KycKybService, PolicyError


class CreateKycCaseRequest(BaseModel):
    state_id: TenantState
    subject_ref: str
    subject_type: SubjectType = SubjectType.CITIZEN_WALLET
    actor: str = "system"
    required_documents: Optional[List[DocumentType]] = None
    liveness_required: bool = True


class AttachDocumentRequest(BaseModel):
    state_id: TenantState
    document_type: DocumentType
    object_uri: str
    sha256: str = Field(min_length=8)
    uploaded_by: str = "system"
    expiry_date: Optional[date] = None


class IssueChallengeRequest(BaseModel):
    state_id: TenantState
    mode: str = "active"
    actor: str = "system"


class SubmitEvidenceRequest(BaseModel):
    state_id: TenantState
    challenge_nonce: str
    artifact_hashes: List[str] = Field(default_factory=list)
    motion_score: float = Field(ge=0.0, le=1.0)
    texture_score: float = Field(ge=0.0, le=1.0)
    depth_score: float = Field(ge=0.0, le=1.0)
    action_completion_score: float = Field(default=1.0, ge=0.0, le=1.0)
    voice_match_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    device_attestation_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    captured_at: datetime = Field(default_factory=utcnow)
    actor: str = "subject"


class CreateKybCaseRequest(BaseModel):
    state_id: TenantState
    legal_name: str
    rc_number: str
    business_type: BusinessType = BusinessType.LIMITED_LIABILITY
    address: str
    tin: Optional[str] = None
    actor: str = "system"


class AddBeneficialOwnerRequest(BaseModel):
    state_id: TenantState
    owner_identity: str
    display_label: str
    ownership_percentage: float = Field(gt=0.0, le=100.0)
    kyc_case_id: Optional[str] = None
    politically_exposed: bool = False
    actor: str = "system"


class ReviewRequest(BaseModel):
    state_id: TenantState
    decision: ReviewDecision
    reviewer: str
    reason: str


class StateRequest(BaseModel):
    state_id: TenantState
    actor: str = "system"


def build_service(mode: Optional[str] = None) -> KycKybService:
    """Build a service for the given mode.

    ``local``/``test`` mode uses deterministic simulated adapters; any other
    mode wires fail-closed production adapter seams.

    Registry federation is governed by ``KYC_REGISTRY_MODE``:

    * ``fixture`` (default) — deterministic :class:`FixtureRegistryAdapter`.
    * ``live`` — constructs NIMC/CAC clients from ``NIMC_*``/``CAC_*`` env
      vars; boot FAILS listing any missing vars (fail-closed at boot; the
      adapters themselves fail closed at call time).
    """
    mode = mode or os.environ.get("KYC_KYB_MODE", "local")
    registries = build_registry_adapters()
    if mode in ("local", "test"):
        return KycKybService(
            ocr_adapter=SimulatedDocumentAIAdapter(ExtractionEngine.PADDLEOCR),
            docling_adapter=SimulatedDocumentAIAdapter(ExtractionEngine.DOCLING),
            vlm_adapter=SimulatedVLMAdapter(),
            **registries,
            liveness_engine=LivenessEngine(),
        )
    return KycKybService(  # production: all seams fail closed unless configured
        ocr_adapter=PaddleOCRAdapter(enabled=os.environ.get("PADDLEOCR_ENABLED") == "1"),
        docling_adapter=DoclingAdapter(enabled=os.environ.get("DOCLING_ENABLED") == "1"),
        vlm_adapter=VLMAdapter(
            enabled=os.environ.get("VLM_ENABLED") == "1",
            endpoint_url=os.environ.get("VLM_ENDPOINT_URL"),
        ),
        **registries,
        liveness_engine=LivenessEngine(),
    )


# Registry federation env wiring (KYC_REGISTRY_MODE=live).
REGISTRY_LIVE_ENV_VARS = (
    "NIMC_BASE_URL",
    "NIMC_CLIENT_ID",
    "NIMC_CLIENT_SECRET",
    "CAC_BASE_URL",
    "CAC_CLIENT_ID",
    "CAC_CLIENT_SECRET",
)


def build_registry_adapters(env: Optional[dict] = None) -> dict:
    """Build registry adapters from ``KYC_REGISTRY_MODE``.

    Default ``fixture`` is deterministic; ``live`` fails closed at boot by
    raising :class:`RuntimeError` listing every missing env var.
    """
    env = os.environ if env is None else env
    mode = env.get("KYC_REGISTRY_MODE", "fixture")
    if mode == "fixture":
        return {
            "corporate_registry": FixtureRegistryAdapter(),
            "identity_registry": FixtureRegistryAdapter(),
            "sanctions": FixtureRegistryAdapter(),
        }
    if mode != "live":
        raise RuntimeError(
            f"unknown KYC_REGISTRY_MODE {mode!r}; expected 'fixture' or 'live'"
        )
    missing = [var for var in REGISTRY_LIVE_ENV_VARS if not env.get(var)]
    if missing:
        raise RuntimeError(
            "KYC_REGISTRY_MODE=live but missing required env vars: "
            + ", ".join(missing)
        )
    from .adapters.registry_clients import CacClient, NimcClient

    timeout_s = float(env.get("REGISTRY_TIMEOUT_S", "10"))
    nimc = NimcClient(
        base_url=env["NIMC_BASE_URL"],
        client_id=env["NIMC_CLIENT_ID"],
        client_secret=env["NIMC_CLIENT_SECRET"],
        timeout_s=timeout_s,
        mtls_cert=env.get("NIMC_MTLS_CERT"),
        mtls_key=env.get("NIMC_MTLS_KEY"),
    )
    cac = CacClient(
        base_url=env["CAC_BASE_URL"],
        client_id=env["CAC_CLIENT_ID"],
        client_secret=env["CAC_CLIENT_SECRET"],
        timeout_s=timeout_s,
        mtls_cert=env.get("CAC_MTLS_CERT"),
        mtls_key=env.get("CAC_MTLS_KEY"),
    )
    return {
        "corporate_registry": CacRegistryAdapter(
            enabled=True, client=cac, timeout_s=timeout_s
        ),
        "identity_registry": NimcAdapter(
            enabled=True, client=nimc, timeout_s=timeout_s
        ),
        # No live sanctions provider wired yet — seam stays fail-closed.
        "sanctions": SanctionsAdapter(enabled=False),
    }


def create_app(service: Optional[KycKybService] = None) -> FastAPI:
    app = FastAPI(title="mod-kyc-kyb — KYC/KYB, Document AI & Liveness")
    app.state.service = service or build_service()

    def svc(request: Request) -> KycKybService:
        return request.app.state.service

    def _map(exc: Exception):
        if isinstance(exc, NotFoundError):
            return HTTPException(404, str(exc))
        if isinstance(exc, TenantMismatchError):
            return HTTPException(403, str(exc))
        if isinstance(exc, (ConflictError, PolicyError)):
            return HTTPException(409, str(exc))
        return HTTPException(500, str(exc))

    # ---------------- health ----------------

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "mod-kyc-kyb"}

    # ---------------- KYC ----------------

    @app.post("/kyc/v1/cases", response_model=KycCase, status_code=201)
    def create_kyc_case(body: CreateKycCaseRequest, request: Request):
        return svc(request).create_kyc_case(
            body.state_id, body.subject_ref, body.subject_type, body.actor,
            body.required_documents, body.liveness_required,
        )

    @app.get("/kyc/v1/cases/{case_id}", response_model=KycCase)
    def get_kyc_case(case_id: str, request: Request, state_id: TenantState = Query(...)):
        try:
            return svc(request).get_kyc_case(case_id, state_id)
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyc/v1/cases/{case_id}/documents", response_model=DocumentArtifact, status_code=201)
    def attach_kyc_document(case_id: str, body: AttachDocumentRequest, request: Request):
        try:
            return svc(request).attach_document(
                body.state_id, case_id, body.document_type, body.object_uri,
                body.sha256, body.uploaded_by, body.expiry_date, case_type="KYC",
            )
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyc/v1/documents/{artifact_id}/extract")
    def extract_document(artifact_id: str, body: StateRequest, request: Request):
        try:
            return svc(request).extract_document(body.state_id, artifact_id, body.actor)
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyc/v1/cases/{case_id}/liveness-challenges", response_model=LivenessChallenge, status_code=201)
    def issue_challenge(case_id: str, body: IssueChallengeRequest, request: Request):
        try:
            return svc(request).issue_liveness_challenge(
                body.state_id, case_id, body.mode, body.actor
            )
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyc/v1/liveness/{challenge_id}/evidence", response_model=LivenessResult)
    def submit_evidence(challenge_id: str, body: SubmitEvidenceRequest, request: Request):
        evidence = LivenessEvidence(
            challenge_nonce=body.challenge_nonce,
            artifact_hashes=body.artifact_hashes,
            motion_score=body.motion_score,
            texture_score=body.texture_score,
            depth_score=body.depth_score,
            action_completion_score=body.action_completion_score,
            voice_match_score=body.voice_match_score,
            device_attestation_score=body.device_attestation_score,
            captured_at=body.captured_at,
        )
        try:
            return svc(request).submit_liveness_evidence(
                body.state_id, challenge_id, evidence, body.actor
            )
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyc/v1/cases/{case_id}/submit", response_model=KycCase)
    def submit_kyc_case(case_id: str, body: StateRequest, request: Request):
        try:
            return svc(request).submit_kyc_case(body.state_id, case_id, body.actor)
        except (NotFoundError, TenantMismatchError, ConflictError) as exc:
            raise _map(exc)

    @app.post("/kyc/v1/cases/{case_id}/review", response_model=KycCase)
    def review_kyc_case(case_id: str, body: ReviewRequest, request: Request):
        try:
            return svc(request).review_case(
                body.state_id, case_id, body.decision, body.reviewer, body.reason, "KYC"
            )
        except (NotFoundError, TenantMismatchError, ConflictError) as exc:
            raise _map(exc)

    @app.get("/kyc/v1/review-queue", response_model=List[ReviewTask])
    def kyc_review_queue(request: Request, state_id: TenantState = Query(...)):
        return svc(request).review_queue(state_id, "KYC")

    # ---------------- KYB ----------------

    @app.post("/kyb/v1/cases", response_model=KybCase, status_code=201)
    def create_kyb_case(body: CreateKybCaseRequest, request: Request):
        return svc(request).create_kyb_case(
            body.state_id, body.legal_name, body.rc_number, body.business_type,
            body.address, body.tin, body.actor,
        )

    @app.get("/kyb/v1/cases/{case_id}", response_model=KybCase)
    def get_kyb_case(case_id: str, request: Request, state_id: TenantState = Query(...)):
        try:
            return svc(request).get_kyb_case(case_id, state_id)
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyb/v1/cases/{case_id}/documents", response_model=DocumentArtifact, status_code=201)
    def attach_kyb_document(case_id: str, body: AttachDocumentRequest, request: Request):
        try:
            return svc(request).attach_document(
                body.state_id, case_id, body.document_type, body.object_uri,
                body.sha256, body.uploaded_by, body.expiry_date, case_type="KYB",
            )
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyb/v1/cases/{case_id}/registry-verification", response_model=List[RegistryVerification])
    def registry_verification(case_id: str, body: StateRequest, request: Request):
        try:
            return svc(request).verify_registry(body.state_id, case_id, body.actor)
        except (NotFoundError, TenantMismatchError) as exc:
            raise _map(exc)

    @app.post("/kyb/v1/cases/{case_id}/beneficial-owners", response_model=KybCase)
    def add_beneficial_owner(case_id: str, body: AddBeneficialOwnerRequest, request: Request):
        try:
            return svc(request).add_beneficial_owner(
                body.state_id, case_id, body.owner_identity, body.display_label,
                body.ownership_percentage, body.kyc_case_id,
                body.politically_exposed, body.actor,
            )
        except (NotFoundError, TenantMismatchError, PolicyError) as exc:
            raise _map(exc)

    @app.post("/kyb/v1/cases/{case_id}/submit", response_model=KybCase)
    def submit_kyb_case(case_id: str, body: StateRequest, request: Request):
        try:
            return svc(request).submit_kyb_case(body.state_id, case_id, body.actor)
        except (NotFoundError, TenantMismatchError, ConflictError) as exc:
            raise _map(exc)

    @app.post("/kyb/v1/cases/{case_id}/review", response_model=KybCase)
    def review_kyb_case(case_id: str, body: ReviewRequest, request: Request):
        try:
            return svc(request).review_case(
                body.state_id, case_id, body.decision, body.reviewer, body.reason, "KYB"
            )
        except (NotFoundError, TenantMismatchError, ConflictError) as exc:
            raise _map(exc)

    @app.get("/kyb/v1/review-queue", response_model=List[ReviewTask])
    def kyb_review_queue(request: Request, state_id: TenantState = Query(...)):
        return svc(request).review_queue(state_id, "KYB")

    # ---------------- shared audit ----------------

    @app.get("/kyc-kyb/v1/audit", response_model=List[AuditEntry])
    def audit_log(request: Request, state_id: TenantState = Query(...)):
        return svc(request).audit_log(state_id)

    @app.get("/kyc-kyb/v1/audit/verify")
    def audit_verify(request: Request, state_id: Optional[TenantState] = Query(None)):
        return {"valid": svc(request).verify_audit(state_id)}

    return app


app = create_app()
