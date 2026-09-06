"""Biometric hardware seam: default local liveness + fail-closed vendor SDK."""
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from app.adapters import (
    AdapterUnavailableError,
    BiometricDeviceAdapter,
    HardwareLivenessAdapter,
    LivenessEngine,
    get_liveness_adapter,
)
from app.domain import LivenessAction, LivenessChallenge, LivenessEvidence


def _env(**overrides):
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(overrides)
    return env


def test_default_adapter_is_local_liveness():
    adapter = get_liveness_adapter(env=_env())
    assert isinstance(adapter, LivenessEngine)
    assert isinstance(adapter, HardwareLivenessAdapter)


def test_vendor_module_missing_fails_closed():
    with pytest.raises(AdapterUnavailableError, match="cannot load vendor SDK"):
        get_liveness_adapter(
            env=_env(KYC_BIOMETRIC_ADAPTER="vendor_sdk_that_does_not_exist")
        )


def test_vendor_module_without_factory_fails_closed(tmp_path, monkeypatch):
    pkg = tmp_path / "fake_vendor_no_factory.py"
    pkg.write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(AdapterUnavailableError):
        BiometricDeviceAdapter("fake_vendor_no_factory")


def _make_challenge_and_evidence():
    now = datetime.now(timezone.utc)
    challenge = LivenessChallenge(
        challenge_id="ch-1",
        tenant_state_id="ogun",
        case_id="case-1",
        nonce="ab" * 16,
        action_sequence=[LivenessAction.BLINK],
        mode="active",
        expires_at=now + timedelta(seconds=300),
        max_attempts=3,
    )
    evidence = LivenessEvidence(
        challenge_nonce=challenge.nonce,
        motion_score=0.9,
        action_completion_score=0.9,
        texture_score=0.9,
        depth_score=0.9,
        device_attestation_score=0.9,
        voice_match_score=None,
        artifact_hashes=["h1"],
        captured_at=now,
    )
    return challenge, evidence


def test_vendor_adapter_delegates_and_matches_protocol():
    """A loadable vendor SDK module must satisfy HardwareLivenessAdapter."""
    challenge, evidence = _make_challenge_and_evidence()
    module = types.ModuleType("fake_vendor_sdk")
    module.create_adapter = lambda: LivenessEngine()
    sys.modules["fake_vendor_sdk"] = module
    try:
        adapter = BiometricDeviceAdapter("fake_vendor_sdk")
        assert isinstance(adapter, HardwareLivenessAdapter)
        result = adapter.evaluate(challenge, evidence)
        assert result.passed is True
        assert result.score >= 0.7
    finally:
        del sys.modules["fake_vendor_sdk"]


def test_local_engine_behaviour_unchanged_via_factory():
    challenge, evidence = _make_challenge_and_evidence()
    adapter = get_liveness_adapter(env=_env())
    result = adapter.evaluate(challenge, evidence)
    assert result.passed is True
    assert result.reasons == ["ok"]
