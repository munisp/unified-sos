"""Perf tests for mod-kyc-kyb (perf-internal assertions only).

Covers: risk-signal scoring reads a module-level weight table (no per-call
rebuild), and the create→submit KYC hot path stays fast under iteration.
"""
from __future__ import annotations

import time

from app.adapters import FixtureRegistryAdapter, LivenessEngine
from app.domain import SubjectType, TenantState
from app.service import KycKybService, SIGNAL_WEIGHTS

TENANT = TenantState.LAGOS if hasattr(TenantState, "LAGOS") else "lagos"


def make_service() -> KycKybService:
    return KycKybService(
        sanctions=FixtureRegistryAdapter(),
        liveness_engine=LivenessEngine(),
    )


def test_score_signals_uses_module_level_weights(monkeypatch):
    # If the mapping were rebuilt inside the function, monkeypatching the
    # module table would have no effect; this proves the hot path reads the
    # shared constant.
    svc = make_service()
    monkeypatch.setitem(SIGNAL_WEIGHTS, "MISSING_DOCUMENTS", 7)
    assert svc._score_signals(["MISSING_DOCUMENTS"]) == 7
    assert svc._score_signals(["MISSING_DOCUMENTS", "UNKNOWN_SIGNAL"]) == 17
    assert svc._score_signals(["SANCTIONS_HIT"]) == 100  # capped


def test_kyc_create_submit_smoke():
    svc = make_service()
    start = time.perf_counter()
    for i in range(200):
        case = svc.create_kyc_case(
            TENANT, f"subject-{i}", SubjectType.CITIZEN_WALLET,
            liveness_required=False,
        )
        svc.submit_kyc_case(TENANT, case.case_id)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0  # generous ceiling; in-memory, no network
    assert len(svc.repo.audit) >= 400
