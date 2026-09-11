"""Conservation-of-value invariants + hash-chained audit evidence."""
import pytest

from ledger.fundsflow.invariants import (
    ConservationEnforcer,
    HashChainAuditLog,
    InvariantViolation,
    FEDERAL_PASS_THROUGH_ACCOUNT,
)


def test_exact_sum_split_passes_and_is_audited():
    enc = ConservationEnforcer()
    enc.enforce_split(100_000, [("CRF", 3001, 65_000), ("MDA", 2010, 35_000)])
    assert enc.audit.events[-1].kind == "CHECK_PASSED"


def test_sum_mismatch_raises_and_is_audit_logged():
    enc = ConservationEnforcer()
    with pytest.raises(InvariantViolation):
        enc.enforce_split(100_000, [("CRF", 3001, 65_000), ("MDA", 2010, 34_999)])
    assert enc.audit.events[-1].kind == "VIOLATION"


@pytest.mark.parametrize("bad", [0, -500])
def test_zero_and_negative_legs_rejected(bad):
    enc = ConservationEnforcer()
    with pytest.raises(InvariantViolation):
        enc.enforce_split(100_000, [("CRF", 3001, 100_000), ("X", 2010, bad)])


def test_federal_pass_through_5001_never_credited():
    enc = ConservationEnforcer()
    with pytest.raises(InvariantViolation, match="5001"):
        enc.enforce_split(
            50_000, [("FAAC", FEDERAL_PASS_THROUGH_ACCOUNT, 50_000)]
        )


def test_non_positive_source_rejected():
    enc = ConservationEnforcer()
    with pytest.raises(InvariantViolation):
        enc.enforce_split(0, [("CRF", 3001, 0)])


def test_compute_split_remainder_to_crf_exact_sum():
    enc = ConservationEnforcer()
    # 65/15/10/5/5 of 100_001 kobo — floor rounding leaves a remainder
    legs = enc.compute_split(
        100_001,
        [
            ("STATE_CONSOLIDATED_REVENUE_FUND", 3001, 65.0),
            ("MDA_RETENTION_ACCOUNT", 2010, 15.0),
            ("PPP_TECH_CONCESSIONAIRE_ESCROW", 2099, 10.0),
            ("LOCAL_GOVERNMENT_SHARE_POOL", 2020, 5.0),
            ("SECURITY_TRUST_FUND", 4001, 5.0),
        ],
    )
    assert sum(a for _, _, a in legs) == 100_001
    crf = next(a for b, _, a in legs if b == "STATE_CONSOLIDATED_REVENUE_FUND")
    assert crf == 65_000 + (100_001 - (65_000 + 15_000 + 10_000 + 5_000 + 5_000))


def test_compute_split_percentages_must_sum_to_100():
    enc = ConservationEnforcer()
    with pytest.raises(InvariantViolation):
        enc.compute_split(1000, [("CRF", 3001, 60.0), ("MDA", 2010, 30.0)])


def test_hash_chain_integrity_and_tamper_detection():
    log = HashChainAuditLog()
    enc = ConservationEnforcer(audit=log)
    enc.enforce_split(10, [("CRF", 3001, 10)])
    with pytest.raises(InvariantViolation):
        enc.enforce_split(10, [("CRF", 3001, 9)])
    assert log.verify() == []
    # tamper: mutate a recorded detail → chain verification must flag it
    log.events[0].detail["source"] = 999999
    assert log.verify(), "tampering must break the hash chain"
