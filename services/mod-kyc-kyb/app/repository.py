"""In-memory tenant-isolated repository with hash-chained audit log."""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .domain import (
    AuditEntry,
    DocumentArtifact,
    ExtractionResult,
    KycCase,
    KybCase,
    LivenessChallenge,
    LivenessResult,
    ReviewTask,
)


class NotFoundError(Exception):
    pass


class TenantMismatchError(Exception):
    pass


class ConflictError(Exception):
    pass


class Repository:
    """Thread-safe in-memory store keyed by (tenant, id)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.kyc_cases: Dict[str, KycCase] = {}
        self.kyb_cases: Dict[str, KybCase] = {}
        self.artifacts: Dict[str, DocumentArtifact] = {}
        self.extractions: Dict[str, List[ExtractionResult]] = {}  # artifact_id -> results
        self.challenges: Dict[str, LivenessChallenge] = {}
        self.liveness_results: Dict[str, LivenessResult] = {}  # challenge_id -> latest
        self.review_tasks: Dict[str, ReviewTask] = {}
        self.audit: List[AuditEntry] = []

    # ---------------- audit ----------------

    def append_audit(
        self, tenant: str, actor: str, action: str,
        entity_type: str, entity_id: str, detail: dict,
    ) -> AuditEntry:
        with self._lock:
            seq = len(self.audit) + 1
            prev_hash = self.audit[-1].entry_hash if self.audit else "GENESIS"
            entry = AuditEntry.create(
                seq, tenant, actor, action, entity_type, entity_id, detail, prev_hash
            )
            self.audit.append(entry)
            return entry

    def audit_for_tenant(self, tenant: str) -> List[AuditEntry]:
        return [e for e in self.audit if e.tenant_state_id == tenant]

    def verify_audit_chain(self, tenant: Optional[str] = None) -> bool:
        entries = self.audit if tenant is None else self.audit_for_tenant(tenant)
        prev = None if tenant is None else self._prev_hash_before(tenant)
        for entry in entries:
            expected_prev = prev if prev is not None else entry.prev_hash
            if entry.prev_hash != expected_prev:
                return False
            if not entry.verify():
                return False
            prev = entry.entry_hash
        return True

    def _prev_hash_before(self, tenant: str) -> Optional[str]:
        idx = next(
            (i for i, e in enumerate(self.audit) if e.tenant_state_id == tenant), None
        )
        if idx is None:
            return None
        return self.audit[idx].prev_hash

    # ---------------- KYC ----------------

    def save_kyc_case(self, case: KycCase) -> KycCase:
        with self._lock:
            self.kyc_cases[case.case_id] = case
        return case

    def get_kyc_case(self, case_id: str, tenant: str) -> KycCase:
        case = self.kyc_cases.get(case_id)
        if case is None:
            raise NotFoundError(f"kyc case not found: {case_id}")
        if case.tenant_state_id != tenant:
            raise TenantMismatchError("cross-tenant access denied")
        return case

    # ---------------- KYB ----------------

    def save_kyb_case(self, case: KybCase) -> KybCase:
        with self._lock:
            self.kyb_cases[case.case_id] = case
        return case

    def get_kyb_case(self, case_id: str, tenant: str) -> KybCase:
        case = self.kyb_cases.get(case_id)
        if case is None:
            raise NotFoundError(f"kyb case not found: {case_id}")
        if case.tenant_state_id != tenant:
            raise TenantMismatchError("cross-tenant access denied")
        return case

    # ---------------- artifacts / extractions ----------------

    def save_artifact(self, artifact: DocumentArtifact) -> DocumentArtifact:
        with self._lock:
            self.artifacts[artifact.artifact_id] = artifact
        return artifact

    def get_artifact(self, artifact_id: str, tenant: str) -> DocumentArtifact:
        art = self.artifacts.get(artifact_id)
        if art is None:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        if art.tenant_state_id != tenant:
            raise TenantMismatchError("cross-tenant access denied")
        return art

    def artifacts_for_case(self, case_id: str) -> List[DocumentArtifact]:
        return [a for a in self.artifacts.values() if a.case_id == case_id]

    def save_extraction(self, artifact_id: str, result: ExtractionResult) -> None:
        with self._lock:
            self.extractions.setdefault(artifact_id, []).append(result)

    def extractions_for(self, artifact_id: str) -> List[ExtractionResult]:
        return list(self.extractions.get(artifact_id, []))

    # ---------------- liveness ----------------

    def save_challenge(self, challenge: LivenessChallenge) -> LivenessChallenge:
        with self._lock:
            self.challenges[challenge.challenge_id] = challenge
        return challenge

    def get_challenge(self, challenge_id: str, tenant: str) -> LivenessChallenge:
        ch = self.challenges.get(challenge_id)
        if ch is None:
            raise NotFoundError(f"challenge not found: {challenge_id}")
        if ch.tenant_state_id != tenant:
            raise TenantMismatchError("cross-tenant access denied")
        return ch

    def challenges_for_case(self, case_id: str) -> List[LivenessChallenge]:
        return [c for c in self.challenges.values() if c.case_id == case_id]

    def save_liveness_result(self, challenge_id: str, result: LivenessResult) -> None:
        with self._lock:
            self.liveness_results[challenge_id] = result

    # ---------------- review queue ----------------

    def save_review_task(self, task: ReviewTask) -> ReviewTask:
        with self._lock:
            self.review_tasks[task.task_id] = task
        return task

    def review_queue(self, tenant: str, case_type: Optional[str] = None) -> List[ReviewTask]:
        return [
            t
            for t in self.review_tasks.values()
            if t.tenant_state_id == tenant
            and t.status == "OPEN"
            and (case_type is None or t.case_type == case_type)
        ]

    def open_task_for_case(self, case_id: str) -> Optional[ReviewTask]:
        for t in self.review_tasks.values():
            if t.case_id == case_id and t.status == "OPEN":
                return t
        return None
