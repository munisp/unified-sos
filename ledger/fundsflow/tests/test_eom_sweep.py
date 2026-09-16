"""EOM sweep: linked-chain execution, idempotent double-run, remainder."""
import pytest

from ledger.fundsflow.eom_sweep import (
    EomSweepError,
    eom_run_key,
    run_eom_sweep,
)
from ledger.fundsflow.tigerbeetle_flows import InMemoryTBClient

CLEARING = 5001
CRF = 3001
LEGS = [("crf_share", CRF, 120_000), ("mda_share", 2010, 45_000),
        ("vat_share", 2020, 9_500)]


def funded_client(gross=200_000):
    c = InMemoryTBClient()
    c.balances[CLEARING] = gross
    return c


def test_run_key_format():
    assert eom_run_key("lasg", "2025-01") == "EOM|lasg|2025-01"


def test_sweep_executes_legs_and_zeroes_clearing():
    c = funded_client()
    store = {}
    report = run_eom_sweep(
        c, tenant="lasg", year_month="2025-01", clearing_account=CLEARING,
        legs=LEGS, crf_account=CRF, store=store, clock=lambda: 123.0,
    )
    assert report["status"] == "COMPLETED"
    assert report["swept_total_kobo"] == 174_500
    # remainder: 200_000 - 174_500 = 25_500 swept to CRF
    assert report["remainder_kobo"] == 25_500
    assert c.balance(CLEARING) == 0
    assert c.balance(2010) == 45_000 and c.balance(2020) == 9_500
    assert c.balance(CRF) == 120_000 + 25_500
    assert report["completed_at"] == 123.0
    assert store["EOM|lasg|2025-01"]["status"] == "COMPLETED"


def test_double_run_is_no_op():
    c = funded_client()
    store = {}
    first = run_eom_sweep(c, tenant="lasg", year_month="2025-01",
                          clearing_account=CLEARING, legs=LEGS,
                          crf_account=CRF, store=store)
    second = run_eom_sweep(c, tenant="lasg", year_month="2025-01",
                           clearing_account=CLEARING, legs=LEGS,
                           crf_account=CRF, store=store)
    assert second["replayed"] is True
    assert second["run_key"] == first["run_key"]
    assert second["transfer_ids"] == first["transfer_ids"]  # same ids
    # no double movement
    assert c.balance(CLEARING) == 0 and c.balance(CRF) == 145_500


def test_retry_without_store_resolves_same_ids_no_double_apply():
    c = funded_client()
    kwargs = dict(tenant="lasg", year_month="2025-01", clearing_account=CLEARING,
                  legs=LEGS, crf_account=CRF)
    first = run_eom_sweep(c, **kwargs)
    # clearing balance is now 0 → retry sweeps nothing new; ids identical
    second = run_eom_sweep(c, **kwargs)
    assert second["transfer_ids"] == first["transfer_ids"]
    # clearing already zero → no new remainder leg, no double-apply
    assert second["remainder_kobo"] == 0
    assert c.balance(CRF) == 145_500 and c.balance(CLEARING) == 0


def test_multi_leg_conservation():
    c = funded_client(gross=174_500)  # exact: no remainder
    report = run_eom_sweep(c, tenant="lasg", year_month="2025-02",
                           clearing_account=CLEARING, legs=LEGS,
                           crf_account=CRF, store={})
    assert report["remainder_kobo"] == 0
    assert report["remainder_transfer_id"] is None
    assert c.balance(CLEARING) == 0
    assert c.balance(CRF) == 120_000


def test_sweep_fails_closed_on_empty_or_bad_legs():
    c = funded_client()
    with pytest.raises(EomSweepError):
        run_eom_sweep(c, tenant="lasg", year_month="2025-01",
                      clearing_account=CLEARING, legs=[], store={})
    with pytest.raises(EomSweepError):
        run_eom_sweep(c, tenant="lasg", year_month="2025-01",
                      clearing_account=CLEARING,
                      legs=[("bad", 2010, 0)], store={})


def test_insufficient_clearing_funds_aborts_atomically():
    c = funded_client(gross=100_000)  # less than sum(legs)
    with pytest.raises(Exception):
        run_eom_sweep(c, tenant="lasg", year_month="2025-01",
                      clearing_account=CLEARING, legs=LEGS, store={})
    # linked chain rolled back wholesale: nothing moved
    assert c.balance(CLEARING) == 100_000
    assert c.balance(2010) == 0 and c.balance(CRF) == 0
