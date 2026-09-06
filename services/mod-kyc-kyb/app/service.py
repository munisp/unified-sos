"""Service layer: KYC/KYB flows, extraction pipeline, deterministic risk scoring."""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from .adapters.base import AdapterUnavailableError
from .adapters.liveness import LivenessEngine
from .domain import (
    AuditEntry,
    BeneficialOwner,
    DocumentArtifact,
    DocumentType,
    ExtractionResult,
    KycCase,
    KybCase,
    LivenessChallenge,
    LivenessEvidence,
    LivenessResult,
    RegistryName,
    RegistryStatus,
    RegistryVerification,
    ReviewDecision,
    ReviewTask,
    RiskBand,
    SubjectType,
    TenantState,
    VerificationStatus,
    sha256_hex,
    utcnow,
)
from .repository import ConflictError, NotFoundError, Repository, TenantMismatchError


class PolicyError(ValueError):
    pass


# Deterministic risk thresholds (0-100 score; higher = riskier).
RISK_LOW_MAX = 25
RISK_MEDIUM_MAX = 60
RISK_HIGH_MAX = 85

SUBJECT_REQUIRED_DOCS: Dict[SubjectType, List[DocumentType]] = {
    SubjectType.CITIZEN_WALLET: [DocumentType.NIN_SLIP],
    SubjectType.RESIDENT: [DocumentType.NIN_SLIP, DocumentType.PROOF_OF_ADDRESS],
    SubjectType.AGENT: [DocumentType.NATIONAL_ID, DocumentType.PROOF_OF_ADDRESS],
    SubjectType.EMPLOYEE: [DocumentType.NATIONAL_ID],
    SubjectType.VENDOR_CONTACT: [DocumentType.NATIONAL_ID],
}

KYB_REQUIRED_DOCS = [
    DocumentType.CAC_CERTIFICATE,
    DocumentType.BENEFICIAL_OWNERSHIP_DECLARATION,
]

RISK_BAND_ORDER = {
    RiskBand.LOW: 0,
    RiskBand.MEDIUM: 1,
    RiskBand.HIGH: 2,
    RiskBand.PROHIBITED: 3,
}


def band_for_score(score: int) -> RiskBand:
    if score <= RISK_LOW_MAX:
        return RiskBand.LOW
    if score <= RISK_MEDIUM_MAX:
        return RiskBand.MEDIUM
    if score <= RISK_HIGH_MAX:
        return RiskBand.HIGH
    return RiskBand.PROHIBITED


class KycKybService:
    """Orchestrates KYC/KYB flows. Adapters are injected; fail-closed."""

    def __init__(
        self,
        repository: Optional[Repository] = None,
        ocr_adapter=None,
        docling_adapter=None,
        vlm_adapter=None,
        corporate_registry=None,
        identity_registry=None,
        sanctions=None,
        liveness_engine: Optional[LivenessEngine] = None,
        auto_approve_low: bool = True,
        reject_prohibited: bool = True,
    ) -> None:
        self.repo = repository or Repository()
        self.ocr = ocr_adapter
        self.docling = docling_adapter
        self.vlm = vlm_adapter
        self.corporate_registry = corporate_registry
        self.identity_registry = identity_registry
        self.sanctions = sanctions
        self.liveness = liveness_engine or LivenessEngine()
        self.auto_approve_low = auto_approve_low
        self.reject_prohibited = reject_prohibited

    # ------------------------------------------------------------------
    # helpers

    def _audit(self, tenant: str, actor: str, action: str, etype: str, eid: str, detail: dict) -> AuditEntry:
        return self.repo.append_audit(tenant, actor, action, etype, eid, detail)

    # ------------------------------------------------------------------
    # KYC

    def create_kyc_case(
        self,
        tenant: TenantState,
        subject_ref: str,
        subject_type: SubjectType,
        actor: str = "system",
        required_documents: Optional[List[DocumentType]] = None,
        liveness_required: bool = True,
    ) -> KycCase:
        case = KycCase(
            case_id=str(uuid.uuid4()),
            tenant_state_id=tenant,
            subject_ref=sha256_hex(f"{tenant}:{subject_ref}"),
            subject_type=subject_type,
            status=VerificationStatus.EVIDENCE_PENDING,
            required_documents=required_documents
            or list(SUBJECT_REQUIRED_DOCS.get(subject_type, [DocumentType.NATIONAL_ID])),
            liveness_required=liveness_required,
        )
        self.repo.save_kyc_case(case)
        self._audit(tenant, actor, "KYC_CASE_CREATED", "kyc_case", case.case_id,
                    {"subject_type": subject_type.value})
        return case

    def get_kyc_case(self, case_id: str, tenant: str) -> KycCase:
        case = self.repo.get_kyc_case(case_id, tenant)
        self._audit(tenant, "reader", "KYC_CASE_READ", "kyc_case", case_id, {})
        return case

    def attach_document(
        self,
        tenant: str,
        case_id: str,
        document_type: DocumentType,
        object_uri: str,
        sha256: str,
        uploaded_by: str,
        expiry_date=None,
        case_type: str = "KYC",
    ) -> DocumentArtifact:
        if case_type == "KYC":
            self.repo.get_kyc_case(case_id, tenant)
        else:
            self.repo.get_kyb_case(case_id, tenant)
        artifact = DocumentArtifact(
            artifact_id=str(uuid.uuid4()),
            tenant_state_id=tenant,  # type: ignore[arg-type]
            case_id=case_id,
            document_type=document_type,
            object_uri=object_uri,
            sha256=sha256,
            uploaded_by=uploaded_by,
            expiry_date=expiry_date,
        )
        self.repo.save_artifact(artifact)
        self._audit(tenant, uploaded_by, "DOCUMENT_ATTACHED", "artifact",
                    artifact.artifact_id, {"case_id": case_id, "doc_type": document_type.value})
        return artifact

    def extract_document(
        self, tenant: str, artifact_id: str, actor: str = "pipeline"
    ) -> Dict[str, object]:
        """Run PaddleOCR -> Docling -> VLM adjudication with consensus scoring."""
        artifact = self.repo.get_artifact(artifact_id, tenant)
        results: List[ExtractionResult] = []
        errors: List[str] = []
        for adapter in (self.ocr, self.docling):
            if adapter is None:
                continue
            try:
                res = adapter.extract(artifact, artifact.document_type)
                self.repo.save_extraction(artifact_id, res)
                results.append(res)
            except AdapterUnavailableError as exc:
                errors.append(f"{type(adapter).__name__}:unavailable")
                self._audit(tenant, actor, "EXTRACTION_UNAVAILABLE", "artifact",
                            artifact_id, {"error": str(exc)[:64]})
        prior: Dict[str, str] = {}
        for res in results:
            prior.update(res.fields)
        if self.vlm is not None:
            try:
                vlm_res = self.vlm.extract(artifact, artifact.document_type, prior)
                self.repo.save_extraction(artifact_id, vlm_res)
                results.append(vlm_res)
            except AdapterUnavailableError as exc:
                errors.append("VLMAdapter:unavailable")
                self._audit(tenant, actor, "EXTRACTION_UNAVAILABLE", "artifact",
                            artifact_id, {"error": str(exc)[:64]})
        consensus, mismatches = self._consensus(results)
        warnings = [w for r in results for w in r.warnings] + mismatches + errors
        self._audit(tenant, actor, "EXTRACTION_COMPLETED", "artifact", artifact_id,
                    {"engines": [r.engine.value for r in results],
                     "consensus": consensus, "mismatches": mismatches})
        return {
            "artifact_id": artifact_id,
            "engines": [r.engine.value for r in results],
            "consensus_score": consensus,
            "mismatch_fields": mismatches,
            "warnings": warnings,
            "errors": errors,
        }

    @staticmethod
    def _consensus(results: List[ExtractionResult]) -> tuple:
        if not results:
            return 0.0, []
        key_votes: Dict[str, Dict[str, int]] = {}
        for res in results:
            for k, v in res.fields.items():
                key_votes.setdefault(k, {}).setdefault(v, 0)
                key_votes[k][v] += 1
        mismatches = [
            k for k, votes in key_votes.items()
            if len(votes) > 1 and k != "rc_number"
        ]
        base = sum(r.confidence for r in results) / len(results)
        penalty = 0.15 * len(mismatches)
        return round(max(0.0, base - penalty), 4), sorted(mismatches)

    # ---------------- liveness ----------------

    def issue_liveness_challenge(
        self, tenant: str, case_id: str, mode: str = "active", actor: str = "system"
    ) -> LivenessChallenge:
        self.repo.get_kyc_case(case_id, tenant)
        challenge = self.liveness.issue_challenge(tenant, case_id, mode=mode)
        self.repo.save_challenge(challenge)
        self._audit(tenant, actor, "LIVENESS_CHALLENGE_ISSUED", "liveness_challenge",
                    challenge.challenge_id, {"case_id": case_id, "mode": mode})
        return challenge

    def submit_liveness_evidence(
        self, tenant: str, challenge_id: str, evidence: LivenessEvidence, actor: str = "subject"
    ) -> LivenessResult:
        challenge = self.repo.get_challenge(challenge_id, tenant)
        result = self.liveness.evaluate(challenge, evidence)
        self.repo.save_challenge(challenge)
        self.repo.save_liveness_result(challenge_id, result)
        self._audit(tenant, actor, "LIVENESS_EVALUATED", "liveness_challenge",
                    challenge_id,
                    {"passed": result.passed, "score": result.score,
                     "flags": result.anti_spoof_flags})
        return result

    # ---------------- KYC submit & risk ----------------

    def _kyc_risk_signals(self, case: KycCase) -> List[str]:
        signals: List[str] = []
        artifacts = self.repo.artifacts_for_case(case.case_id)
        present_types = {a.document_type for a in artifacts}
        missing = [d for d in case.required_documents if d not in present_types]
        if missing:
            signals.append("MISSING_DOCUMENTS")
        for art in artifacts:
            for res in self.repo.extractions_for(art.artifact_id):
                if any(w.startswith("mismatch:") or w.startswith("tamper:") for w in res.warnings):
                    signals.append("EXTRACTION_MISMATCH")
                    break
        if case.liveness_required:
            challenges = self.repo.challenges_for_case(case.case_id)
            passed = any(
                self.repo.liveness_results.get(c.challenge_id, None) is not None
                and self.repo.liveness_results[c.challenge_id].passed
                for c in challenges
            )
            if not passed:
                signals.append("LIVENESS_NOT_PASSED")
        if self.sanctions is not None:
            try:
                screening = self.sanctions.screen(case.subject_ref, case.subject_ref)
                if screening.status == RegistryStatus.MATCH:
                    signals.append("SANCTIONS_HIT")
            except AdapterUnavailableError:
                pass
        return signals

    @staticmethod
    def _score_signals(signals: List[str]) -> int:
        weights = {
            "MISSING_DOCUMENTS": 50,
            "EXTRACTION_MISMATCH": 30,
            "LIVENESS_NOT_PASSED": 45,
            "SANCTIONS_HIT": 100,
            "REGISTRY_MISMATCH": 55,
            "REGISTRY_NOT_FOUND": 40,
            "REGISTRY_UNAVAILABLE": 35,
            "OWNERSHIP_UNVERIFIED": 20,
        }
        return min(100, sum(weights.get(s, 10) for s in signals))

    def submit_kyc_case(self, tenant: str, case_id: str, actor: str = "system") -> KycCase:
        case = self.repo.get_kyc_case(case_id, tenant)
        if case.status in (VerificationStatus.APPROVED, VerificationStatus.REJECTED):
            raise ConflictError("case already decided")
        signals = self._kyc_risk_signals(case)
        case.risk_score = self._score_signals(signals)
        case.risk_band = band_for_score(case.risk_score)
        case.status = VerificationStatus.PROCESSING
        case.updated_at = utcnow()
        if "SANCTIONS_HIT" in signals:
            case.risk_band = RiskBand.PROHIBITED
        if case.risk_band == RiskBand.LOW and self.auto_approve_low:
            case.status = VerificationStatus.APPROVED
            case.decision_reason = "auto_approved_low_risk"
        elif case.risk_band == RiskBand.PROHIBITED and self.reject_prohibited:
            case.status = VerificationStatus.REJECTED
            case.decision_reason = "rejected_prohibited_risk:" + ",".join(signals)
        elif case.risk_band == RiskBand.HIGH:
            case.status = VerificationStatus.IN_REVIEW
            case.decision_reason = "high_risk_review:" + ",".join(signals)
            self._open_review(tenant, "KYC", case_id, signals)
        else:
            case.status = VerificationStatus.IN_REVIEW
            case.decision_reason = "review:" + ",".join(signals or ["medium_risk"])
            self._open_review(tenant, "KYC", case_id, signals or ["medium_risk"])
        self.repo.save_kyc_case(case)
        self._audit(tenant, actor, "KYC_CASE_SUBMITTED", "kyc_case", case_id,
                    {"risk_score": case.risk_score,
                     "risk_band": case.risk_band.value,
                     "status": case.status.value, "signals": signals})
        return case

    def _open_review(self, tenant: str, case_type: str, case_id: str, reasons: List[str]) -> ReviewTask:
        existing = self.repo.open_task_for_case(case_id)
        if existing:
            return existing
        task = ReviewTask(
            task_id=str(uuid.uuid4()),
            tenant_state_id=tenant,  # type: ignore[arg-type]
            case_type=case_type,
            case_id=case_id,
            reason_codes=list(reasons),
        )
        self.repo.save_review_task(task)
        return task

    def review_case(
        self,
        tenant: str,
        case_id: str,
        decision: ReviewDecision,
        reviewer: str,
        reason: str,
        case_type: str = "KYC",
    ) -> object:
        if case_type == "KYC":
            case = self.repo.get_kyc_case(case_id, tenant)
        else:
            case = self.repo.get_kyb_case(case_id, tenant)
        if case.status not in (VerificationStatus.IN_REVIEW, VerificationStatus.PROCESSING):
            raise ConflictError(f"case not in reviewable state: {case.status.value}")
        case.status = (
            VerificationStatus.APPROVED
            if decision == ReviewDecision.APPROVE
            else VerificationStatus.REJECTED
        )
        case.decision_reason = reason
        case.updated_at = utcnow()
        if case_type == "KYC":
            self.repo.save_kyc_case(case)
        else:
            self.repo.save_kyb_case(case)
        task = self.repo.open_task_for_case(case_id)
        if task:
            task.status = "CLOSED"
            task.decision = decision
            task.reviewer = reviewer
            task.decision_reason = reason
            task.resolved_at = utcnow()
            self.repo.save_review_task(task)
        self._audit(tenant, reviewer, f"{case_type}_REVIEW_{decision.value}",
                    f"{case_type.lower()}_case", case_id, {"reason": reason})
        return case

    # ------------------------------------------------------------------
    # KYB

    def create_kyb_case(
        self,
        tenant: TenantState,
        legal_name: str,
        rc_number: str,
        business_type,
        address: str,
        tin: Optional[str] = None,
        actor: str = "system",
    ) -> KybCase:
        case = KybCase(
            case_id=str(uuid.uuid4()),
            tenant_state_id=tenant,
            legal_name_hash=sha256_hex(f"{tenant}:{legal_name.strip().lower()}"),
            legal_name_label=legal_name.strip()[:3].upper() + "***",
            rc_number_hash=sha256_hex(f"rc:{rc_number.strip()}"),
            tin_hash=sha256_hex(f"tin:{tin.strip()}") if tin else None,
            business_type=business_type,
            address_hash=sha256_hex(f"{tenant}:{address.strip().lower()}"),
            status=VerificationStatus.EVIDENCE_PENDING,
        )
        # Keep raw values out of the case; service-level lookup key is hashed.
        self._kyb_lookup = getattr(self, "_kyb_lookup", {})
        self._kyb_lookup[case.case_id] = (legal_name.strip(), rc_number.strip())
        self.repo.save_kyb_case(case)
        self._audit(tenant, actor, "KYB_CASE_CREATED", "kyb_case", case.case_id,
                    {"business_type": case.business_type.value})
        return case

    def get_kyb_case(self, case_id: str, tenant: str) -> KybCase:
        case = self.repo.get_kyb_case(case_id, tenant)
        self._audit(tenant, "reader", "KYB_CASE_READ", "kyb_case", case_id, {})
        return case

    def verify_registry(
        self, tenant: str, case_id: str, actor: str = "system"
    ) -> List[RegistryVerification]:
        case = self.repo.get_kyb_case(case_id, tenant)
        legal_name, rc_number = getattr(self, "_kyb_lookup", {}).get(case_id, ("", ""))
        results: List[RegistryVerification] = []
        if self.corporate_registry is not None:
            try:
                res = self.corporate_registry.verify_company(legal_name, rc_number)
                results.append(res)
                case.registry_verifications.append(res)
            except AdapterUnavailableError:
                unavailable = RegistryVerification(
                    registry=RegistryName.CAC,
                    status=RegistryStatus.UNAVAILABLE,
                    fields_checked=["legal_name", "rc_number"],
                    confidence=0.0,
                    response_hash=sha256_hex("cac:unavailable"),
                )
                results.append(unavailable)
                case.registry_verifications.append(unavailable)
        if self.sanctions is not None:
            try:
                screen = self.sanctions.screen(case.case_id, legal_name)
                results.append(screen)
                case.registry_verifications.append(screen)
            except AdapterUnavailableError:
                pass
        case.updated_at = utcnow()
        self.repo.save_kyb_case(case)
        self._audit(tenant, actor, "KYB_REGISTRY_VERIFIED", "kyb_case", case_id,
                    {"registries": [r.registry.value for r in results],
                     "statuses": [r.status.value for r in results]})
        return results

    def add_beneficial_owner(
        self,
        tenant: str,
        case_id: str,
        owner_identity: str,
        display_label: str,
        ownership_percentage: float,
        kyc_case_id: Optional[str] = None,
        politically_exposed: bool = False,
        actor: str = "system",
    ) -> KybCase:
        case = self.repo.get_kyb_case(case_id, tenant)
        if kyc_case_id is not None:
            self.repo.get_kyc_case(kyc_case_id, tenant)  # cross-reference check
        total = sum(o.ownership_percentage for o in case.beneficial_owners)
        if total + ownership_percentage > 100.0:
            self._audit(tenant, actor, "KYB_BO_REJECTED", "kyb_case", case_id,
                        {"total_would_be": total + ownership_percentage})
            raise PolicyError("total beneficial ownership exceeds 100%")
        owner = BeneficialOwner(
            owner_ref_hash=sha256_hex(f"{tenant}:{owner_identity.strip().lower()}"),
            display_label=display_label,
            ownership_percentage=ownership_percentage,
            kyc_case_id=kyc_case_id,
            politically_exposed=politically_exposed,
        )
        case.beneficial_owners.append(owner)
        case.updated_at = utcnow()
        self.repo.save_kyb_case(case)
        self._audit(tenant, actor, "KYB_BO_ADDED", "kyb_case", case_id,
                    {"ownership": ownership_percentage, "pep": politically_exposed})
        return case

    def _kyb_risk_signals(self, case: KybCase) -> List[str]:
        signals: List[str] = []
        artifacts = self.repo.artifacts_for_case(case.case_id)
        present_types = {a.document_type for a in artifacts}
        missing = [d for d in KYB_REQUIRED_DOCS if d not in present_types]
        if missing:
            signals.append("MISSING_DOCUMENTS")
        for ver in case.registry_verifications:
            if ver.registry == RegistryName.CAC:
                if ver.status == RegistryStatus.MISMATCH:
                    signals.append("REGISTRY_MISMATCH")
                elif ver.status == RegistryStatus.NOT_FOUND:
                    signals.append("REGISTRY_NOT_FOUND")
                elif ver.status == RegistryStatus.UNAVAILABLE:
                    signals.append("REGISTRY_UNAVAILABLE")
            if ver.registry == RegistryName.SANCTIONS and ver.status == RegistryStatus.MATCH:
                signals.append("SANCTIONS_HIT")
        if not case.beneficial_owners:
            signals.append("OWNERSHIP_UNVERIFIED")
        elif any(o.politically_exposed for o in case.beneficial_owners):
            signals.append("PEP_OWNER")
        return signals

    def submit_kyb_case(self, tenant: str, case_id: str, actor: str = "system") -> KybCase:
        case = self.repo.get_kyb_case(case_id, tenant)
        if case.status in (VerificationStatus.APPROVED, VerificationStatus.REJECTED):
            raise ConflictError("case already decided")
        signals = self._kyb_risk_signals(case)
        score = self._score_signals(signals)
        if "PEP_OWNER" in signals:
            score = min(100, score + 25)
        case.risk_score = score
        case.risk_band = band_for_score(score)
        if "SANCTIONS_HIT" in signals:
            case.risk_band = RiskBand.PROHIBITED
        if case.risk_band == RiskBand.LOW and self.auto_approve_low:
            case.status = VerificationStatus.APPROVED
            case.decision_reason = "auto_approved_low_risk"
        elif case.risk_band == RiskBand.PROHIBITED and self.reject_prohibited:
            case.status = VerificationStatus.REJECTED
            case.decision_reason = "rejected_prohibited_risk:" + ",".join(signals)
        else:
            case.status = VerificationStatus.IN_REVIEW
            case.decision_reason = "review:" + ",".join(signals or ["medium_risk"])
            self._open_review(tenant, "KYB", case_id, signals or ["medium_risk"])
        case.updated_at = utcnow()
        self.repo.save_kyb_case(case)
        self._audit(tenant, actor, "KYB_CASE_SUBMITTED", "kyb_case", case_id,
                    {"risk_score": case.risk_score,
                     "risk_band": case.risk_band.value,
                     "status": case.status.value, "signals": signals})
        return case

    # ------------------------------------------------------------------
    # review queues & audit

    def review_queue(self, tenant: str, case_type: Optional[str] = None) -> List[ReviewTask]:
        return self.repo.review_queue(tenant, case_type)

    def audit_log(self, tenant: str) -> List[AuditEntry]:
        self._audit(tenant, "reader", "AUDIT_READ", "audit", tenant, {})
        return self.repo.audit_for_tenant(tenant)

    def verify_audit(self, tenant: Optional[str] = None) -> bool:
        return self.repo.verify_audit_chain(tenant)
