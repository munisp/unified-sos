"""TigerBeetle two-phase semantics: pending/post/void, linked chains, IDs."""
import pytest

from ledger.fundsflow.tigerbeetle_flows import (
    AdapterUnavailableError,
    InMemoryTBClient,
    TransferError,
    build_hold_chain,
    deterministic_transfer_id,
    select_client,
)

LEGS = [("crf", 3001, 65_000), ("mda", 2010, 35_000)]


def funded_client():
    c = InMemoryTBClient()
    c.balances[1001] = 1_000_000  # payer collection account
    return c


def test_deterministic_transfer_id_stable_and_leg_scoped():
    a = deterministic_transfer_id("pay-1", "crf")
    assert a == deterministic_transfer_id("pay-1", "crf")
    assert a != deterministic_transfer_id("pay-1", "mda")
    assert a != deterministic_transfer_id("pay-2", "crf")
    assert 0 <= a < 2**128


def test_hold_then_post_commits_exactly_once():
    c = funded_client()
    chain = build_hold_chain(idempotency_key="p1", source_account=1001, legs=LEGS)
    c.create_transfers(chain)
    assert c.balance(3001) == 0  # pending: not yet credited
    ids = [t.id for t in chain]
    c.post_pending_transfers(ids)
    c.post_pending_transfers(ids)  # idempotent re-post
    assert c.balance(3001) == 65_000 and c.balance(2010) == 35_000
    assert c.balance(1001) == 1_000_000 - 100_000


def test_void_rolls_back_hold_with_zero_balance_change():
    c = funded_client()
    chain = build_hold_chain(idempotency_key="p2", source_account=1001, legs=LEGS)
    c.create_transfers(chain)
    c.void_pending_transfers([t.id for t in chain])
    assert c.balance(1001) == 1_000_000
    assert c.balance(3001) == 0 and c.balance(2010) == 0
    with pytest.raises(TransferError):
        c.post_pending_transfers([t.id for t in chain])  # voided ≠ postable


def test_linked_chain_failure_rolls_back_entire_chain():
    c = funded_client()
    c.fail_on_leg = "mda"  # crash creating the second leg
    chain = build_hold_chain(idempotency_key="p3", source_account=1001, legs=LEGS)
    with pytest.raises(TransferError):
        c.create_transfers(chain)
    assert c.transfers == {}  # atomic: first leg rolled back too


def test_create_replay_is_idempotent_no_double_hold():
    c = funded_client()
    chain = build_hold_chain(idempotency_key="p4", source_account=1001, legs=LEGS)
    c.create_transfers(chain)
    again = build_hold_chain(idempotency_key="p4", source_account=1001, legs=LEGS)
    c.create_transfers(again)  # same IDs → replay, no new transfers
    assert len(c.transfers) == 2
    c.post_pending_transfers([t.id for t in chain])
    assert c.balance(1001) == 900_000  # held/posted exactly once


def test_insufficient_funds_rejects_hold():
    c = InMemoryTBClient()
    c.balances[1001] = 10
    chain = build_hold_chain(idempotency_key="p5", source_account=1001, legs=LEGS)
    with pytest.raises(TransferError):
        c.create_transfers(chain)


def test_cannot_void_posted_transfer():
    c = funded_client()
    chain = build_hold_chain(idempotency_key="p6", source_account=1001, legs=LEGS)
    c.create_transfers(chain)
    c.post_pending_transfers([t.id for t in chain])
    with pytest.raises(TransferError, match="reversal"):
        c.void_pending_transfers([t.id for t in chain])


def test_select_client_fail_closed_in_production(monkeypatch):
    monkeypatch.delenv("SOS_TB_ADDRESSES", raising=False)
    monkeypatch.setenv("SOS_PROFILE", "production")
    with pytest.raises(AdapterUnavailableError):
        select_client()
