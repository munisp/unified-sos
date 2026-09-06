"""Next-generation liveness engine: active challenge + passive modes.

Deterministic scoring combining motion, blink/action completion, texture
(anti-print), depth, device attestation, and optional voice passphrase match.
Only scores/hashes are handled — never biometric media.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from ..domain import (
    LivenessAction,
    LivenessChallenge,
    LivenessChallengeStatus,
    LivenessEvidence,
    LivenessResult,
)

LIVENESS_MODEL_VERSION = "liveness-2.0"
DEFAULT_WEIGHTS: Dict[str, float] = {
    "motion": 0.25,
    "action": 0.20,
    "texture": 0.20,
    "depth": 0.20,
    "device": 0.10,
    "voice": 0.05,
}
PASS_THRESHOLD = 0.70


class LivenessEngine:
    """Challenge generation and deterministic evidence scoring."""

    def __init__(
        self,
        pass_threshold: float = PASS_THRESHOLD,
        weights: Optional[Dict[str, float]] = None,
        challenge_ttl_seconds: int = 300,
        max_attempts: int = 3,
        default_actions: Optional[List[LivenessAction]] = None,
    ) -> None:
        self.pass_threshold = pass_threshold
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.max_attempts = max_attempts
        self.default_actions = default_actions or [
            LivenessAction.BLINK,
            LivenessAction.TURN_LEFT,
            LivenessAction.TURN_RIGHT,
        ]
        self._seen_artifact_hashes: Set[str] = set()

    # Configurable action sequence per tenant/policy.
    TENANT_ACTIONS: Dict[str, List[LivenessAction]] = {}

    def actions_for_tenant(self, tenant_state_id: str) -> List[LivenessAction]:
        return list(self.TENANT_ACTIONS.get(tenant_state_id, self.default_actions))

    def issue_challenge(
        self,
        tenant_state_id: str,
        case_id: str,
        mode: str = "active",
        now: Optional[datetime] = None,
    ) -> LivenessChallenge:
        now = now or datetime.now(timezone.utc)
        actions = (
            self.actions_for_tenant(tenant_state_id)
            if mode == "active"
            else []  # passive mode: no prompted actions
        )
        return LivenessChallenge(
            challenge_id=str(uuid.uuid4()),
            tenant_state_id=tenant_state_id,  # type: ignore[arg-type]
            case_id=case_id,
            nonce=secrets.token_hex(16),
            action_sequence=actions,
            mode=mode,
            expires_at=now + timedelta(seconds=self.challenge_ttl_seconds),
            max_attempts=self.max_attempts,
        )

    def score(self, evidence: LivenessEvidence) -> Tuple[float, List[str]]:
        """Deterministic weighted score; returns (score, anti_spoof_flags)."""
        w = self.weights
        flags: List[str] = []
        device = evidence.device_attestation_score
        voice = evidence.voice_match_score
        device_component = device if device is not None else 0.5
        voice_component = voice if voice is not None else 0.5
        score = (
            w["motion"] * evidence.motion_score
            + w["action"] * evidence.action_completion_score
            + w["texture"] * evidence.texture_score
            + w["depth"] * evidence.depth_score
            + w["device"] * device_component
            + w["voice"] * voice_component
        )
        if evidence.texture_score < 0.3:
            flags.append("PRINT_ARTIFACT_SUSPECTED")
        if evidence.depth_score < 0.3:
            flags.append("FLAT_SURFACE_SUSPECTED")
        if device is not None and device < 0.3:
            flags.append("DEVICE_ATTESTATION_WEAK")
        return round(min(1.0, max(0.0, score)), 4), flags

    def evaluate(
        self,
        challenge: LivenessChallenge,
        evidence: LivenessEvidence,
        now: Optional[datetime] = None,
    ) -> LivenessResult:
        now = now or datetime.now(timezone.utc)
        reasons: List[str] = []

        def result(reason: str, final: bool) -> LivenessResult:
            if final:
                challenge.status = LivenessChallengeStatus.FAILED
            return LivenessResult(
                passed=False, score=0.0, anti_spoof_flags=[],
                model_version=LIVENESS_MODEL_VERSION, reasons=[reason],
            )

        if challenge.status in (
            LivenessChallengeStatus.PASSED,
            LivenessChallengeStatus.FAILED,
        ):
            return result("challenge_already_concluded", final=False)
        if now > challenge.expires_at:
            challenge.status = LivenessChallengeStatus.EXPIRED
            return result("challenge_expired", final=False)
        if evidence.challenge_nonce != challenge.nonce:
            return result("nonce_mismatch", final=False)
        if challenge.attempts >= challenge.max_attempts:
            return result("max_attempts_exceeded", final=True)
        if evidence.captured_at > now + timedelta(seconds=5):
            return result("impossible_timestamp", final=False)
        if evidence.captured_at < challenge.created_at - timedelta(seconds=5):
            return result("impossible_timestamp", final=False)
        challenge.attempts += 1
        reused = [h for h in evidence.artifact_hashes if h in self._seen_artifact_hashes]
        if reused:
            return result("artifact_hash_reuse", final=True)
        score, flags = self.score(evidence)
        for h in evidence.artifact_hashes:
            self._seen_artifact_hashes.add(h)
        if score < self.pass_threshold:
            reasons.append(f"score_below_threshold:{score:.4f}<{self.pass_threshold}")
            if challenge.attempts >= challenge.max_attempts:
                challenge.status = LivenessChallengeStatus.FAILED
                reasons.append("max_attempts_exceeded")
            return LivenessResult(
                passed=False, score=score, anti_spoof_flags=flags,
                model_version=LIVENESS_MODEL_VERSION, reasons=reasons,
            )
        challenge.status = LivenessChallengeStatus.PASSED
        return LivenessResult(
            passed=True, score=score, anti_spoof_flags=flags,
            model_version=LIVENESS_MODEL_VERSION, reasons=["ok"],
        )
