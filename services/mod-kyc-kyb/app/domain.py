"""Domain model for mod-kyc-kyb.

NDPA data-minimization: no raw PII, biometrics, images, videos, or OCR text
are stored in the domain layer beyond hashed references and minimum fields.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


TenantState = Literal["lagos", "ogun", "osun", "benue", "nasarawa", "taraba"]
TENANT_STATES = ("lagos", "ogun", "osun", "benue", "nasarawa", "taraba")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VerificationStatus(str, Enum):
    DRAFT = "DRAFT"
    EVIDENCE_PENDING = "EVIDENCE_PENDING"
    PROCESSING = "PROCESSING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


class RiskBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PROHIBITED = "PROHIBITED"


class DocumentType(str, Enum):
    NIN_SLIP = "NIN_SLIP"
    NATIONAL_ID = "NATIONAL_ID"
    PASSPORT = "PASSPORT"
    DRIVERS_LICENSE = "DRIVERS_LICENSE"
    VOTER_CARD = "VOTER_CARD"
    PROOF_OF_ADDRESS = "PROOF_OF_ADDRESS"
    CAC_CERTIFICATE = "CAC_CERTIFICATE"
    TAX_CLEARANCE = "TAX_CLEARANCE"
    BOARD_RESOLUTION = "BOARD_RESOLUTION"
    BENEFICIAL_OWNERSHIP_DECLARATION = "BENEFICIAL_OWNERSHIP_DECLARATION"
    OTHER = "OTHER"


class ExtractionEngine(str, Enum):
    PADDLEOCR = "PADDLEOCR"
    DOCLING = "DOCLING"
    VLM = "VLM"
    SIMULATED = "SIMULATED"


class LivenessAction(str, Enum):
    BLINK = "BLINK"
    TURN_LEFT = "TURN_LEFT"
    TURN_RIGHT = "TURN_RIGHT"
    SMILE = "SMILE"
    SPEAK_PASSPHRASE = "SPEAK_PASSPHRASE"


class LivenessChallengeStatus(str, Enum):
    ISSUED = "ISSUED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class RegistryName(str, Enum):
    CAC = "CAC"
    NIMC = "NIMC"
    TAX = "TAX"
    SANCTIONS = "SANCTIONS"


class RegistryStatus(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"


class ReviewDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


# ------------------------- shared artifacts -------------------------


class DocumentArtifact(BaseModel):
    """Reference to a stored document. No raw bytes, ever."""

    artifact_id: str
    tenant_state_id: TenantState
    case_id: str
    document_type: DocumentType
    object_uri: str
    sha256: str
    uploaded_by: str
    uploaded_at: datetime = Field(default_factory=utcnow)
    expiry_date: Optional[date] = None


class ExtractionResult(BaseModel):
    engine: ExtractionEngine
    fields: Dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    latency_ms: int = 0
    warnings: List[str] = Field(default_factory=list)
    source_hash: str = ""  # sha256 of the source artifact content


# ------------------------- audit chain -------------------------


class AuditEntry(BaseModel):
    seq: int
    tenant_state_id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    detail_hash: str  # hash of structured detail, never raw PII
    prev_hash: str
    entry_hash: str
    created_at: datetime = Field(default_factory=utcnow)

    @staticmethod
    def compute_hash(
        seq: int, tenant: str, actor: str, action: str, entity_type: str,
        entity_id: str, detail_hash: str, prev_hash: str, created_at: datetime,
    ) -> str:
        canonical = json.dumps(
            {
                "seq": seq,
                "tenant_state_id": tenant,
                "actor": actor,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "detail_hash": detail_hash,
                "prev_hash": prev_hash,
                "created_at": created_at.isoformat(),
            },
            sort_keys=True,
        )
        return sha256_hex(canonical)

    @classmethod
    def create(
        cls, seq: int, tenant: str, actor: str, action: str,
        entity_type: str, entity_id: str, detail: dict, prev_hash: str,
    ) -> "AuditEntry":
        detail_hash = sha256_hex(json.dumps(detail, sort_keys=True, default=str))
        created = utcnow()
        entry_hash = cls.compute_hash(
            seq, tenant, actor, action, entity_type, entity_id,
            detail_hash, prev_hash, created,
        )
        return cls(
            seq=seq, tenant_state_id=tenant, actor=actor, action=action,
            entity_type=entity_type, entity_id=entity_id,
            detail_hash=detail_hash, prev_hash=prev_hash,
            entry_hash=entry_hash, created_at=created,
        )

    def verify(self) -> bool:
        expected = self.compute_hash(
            self.seq, self.tenant_state_id, self.actor, self.action,
            self.entity_type, self.entity_id, self.detail_hash,
            self.prev_hash, self.created_at,
        )
        return hmac.compare_digest(expected, self.entry_hash)


# ------------------------- KYC -------------------------


class SubjectType(str, Enum):
    CITIZEN_WALLET = "CITIZEN_WALLET"
    RESIDENT = "RESIDENT"
    AGENT = "AGENT"
    EMPLOYEE = "EMPLOYEE"
    VENDOR_CONTACT = "VENDOR_CONTACT"


class KycCase(BaseModel):
    case_id: str
    tenant_state_id: TenantState
    subject_ref: str
    subject_type: SubjectType
    status: VerificationStatus = VerificationStatus.DRAFT
    risk_score: int = 0  # 0-100 deterministic score
    risk_band: Optional[RiskBand] = None
    required_documents: List[DocumentType] = Field(default_factory=list)
    liveness_required: bool = True
    decision_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LivenessChallenge(BaseModel):
    challenge_id: str
    tenant_state_id: TenantState
    case_id: str
    nonce: str
    action_sequence: List[LivenessAction]
    mode: str = "active"  # "active" | "passive"
    expires_at: datetime
    max_attempts: int = 3
    attempts: int = 0
    status: LivenessChallengeStatus = LivenessChallengeStatus.ISSUED
    created_at: datetime = Field(default_factory=utcnow)


class LivenessEvidence(BaseModel):
    """Only scores and hashes; never biometric media."""

    challenge_nonce: str
    artifact_hashes: List[str] = Field(default_factory=list)
    motion_score: float = Field(ge=0.0, le=1.0)
    texture_score: float = Field(ge=0.0, le=1.0)
    depth_score: float = Field(ge=0.0, le=1.0)
    action_completion_score: float = Field(default=1.0, ge=0.0, le=1.0)
    voice_match_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    device_attestation_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    captured_at: datetime


class LivenessResult(BaseModel):
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    anti_spoof_flags: List[str] = Field(default_factory=list)
    model_version: str
    reasons: List[str] = Field(default_factory=list)


# ------------------------- KYB -------------------------


class BusinessType(str, Enum):
    LIMITED_LIABILITY = "LIMITED_LIABILITY"
    BUSINESS_NAME = "BUSINESS_NAME"
    INCORPORATED_TRUSTEES = "INCORPORATED_TRUSTEES"
    PARTNERSHIP = "PARTNERSHIP"
    PUBLIC_COMPANY = "PUBLIC_COMPANY"
    OTHER = "OTHER"


class BeneficialOwner(BaseModel):
    """Owner reference: name stored only as hash; minimum fields only."""

    owner_ref_hash: str  # sha256 of canonical owner identity string
    display_label: str  # e.g. initials or role label, not full name
    ownership_percentage: float = Field(gt=0.0, le=100.0)
    kyc_case_id: Optional[str] = None
    politically_exposed: bool = False


class RegistryVerification(BaseModel):
    registry: RegistryName
    status: RegistryStatus
    fields_checked: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    response_hash: str  # hash of registry response; raw payload never stored
    checked_at: datetime = Field(default_factory=utcnow)


class KybCase(BaseModel):
    case_id: str
    tenant_state_id: TenantState
    legal_name_hash: str  # hash of legal name (data minimization)
    legal_name_label: str  # display label (non-sensitive trade label)
    rc_number_hash: str
    tin_hash: Optional[str] = None
    business_type: BusinessType
    address_hash: str
    status: VerificationStatus = VerificationStatus.DRAFT
    risk_score: int = 0
    risk_band: Optional[RiskBand] = None
    registry_verifications: List[RegistryVerification] = Field(default_factory=list)
    directors: List[str] = Field(default_factory=list)  # hashed refs
    beneficial_owners: List[BeneficialOwner] = Field(default_factory=list)
    decision_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ReviewTask(BaseModel):
    task_id: str
    tenant_state_id: TenantState
    case_type: str  # "KYC" | "KYB"
    case_id: str
    reason_codes: List[str] = Field(default_factory=list)
    status: str = "OPEN"  # OPEN | CLOSED
    decision: Optional[ReviewDecision] = None
    reviewer: Optional[str] = None
    decision_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None
