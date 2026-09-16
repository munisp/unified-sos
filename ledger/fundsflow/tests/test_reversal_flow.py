"""Reversal flow: reverse posted chains, reject pending-only, idempotent."""
import pytest

from ledger.fundsflow.tigerbeetle_flows import (
    InMemoryTBClient,
    TransferError,
    build_hold_chain,
    deterministic_transfer_id,
    reverse_chain,
)

LEGS3 = [("crf", 3001, 65_000), ("mda", 2010, 35_000), ("vat", 2020, 7_500)]


def funded_client():
    c = InMemoryTBClient()
    c.balances[1001] = 1_000_000
    return c


def posted_chain(client, key="pay-rev-1", legs=LEGS3):
    chain = build_hold_chain(idempotency_key=key, source_account=1001, legs=legs)
    client.create_transfers(chain)
    client.post_pending_transfers([t.id for t in chain])
    return chain


def test_reverse_posted_three_leg_split_restores_balances():
    c = funded_client()
    chain = posted_chain(c)
    assert c.balance(3001) == 65_000 and c.balance(1001) == 1_000_000 - 107_500

    reversal = reverse_chain(
        c, original_idempotency_key="pay-rev-1", original_chain=chain
    )
    assert len(reversal) == 3
    # accounts swapped on every leg
    for orig, rev in zip(chain, reversal):
        assert rev.debit_account == orig.credit_account
        assert rev.credit_account == orig.debit_account
        assert rev.amount == orig.amount
        assert rev.id == deterministic_transfer_id("pay-rev-1", f"{orig.leg}|reversal")
    # conservation: reversal total == original total, balances restored
    assert sum(t.amount for t in reversal) == sum(t.amount for t in chain)
    assert c.balance(1001) == 1_000_000
    assert c.balance(3001) == 0 and c.balance(2010) == 0 and c.balance(2020) == 0


def test_reverse_pending_only_chain_raises():
    c = funded_client()
    chain = build_hold_chain(idempotency_key="pay-pend", source_account=1001, legs=LEGS3)
    c.create_transfers(chain)  # PENDING, never posted
    with pytest.raises(TransferError, match="PENDING"):
        reverse_chain(c, original_idempotency_key="pay-pend", original_chain=chain)


def test_reverse_chain_idempotent_replay_returns_same_ids():
    c = funded_client()
    chain = posted_chain(c, key="pay-replay")
    first = reverse_chain(c, original_idempotency_key="pay-replay", original_chain=chain)
    second = reverse_chain(c, original_idempotency_key="pay-replay", original_chain=chain)
    assert [t.id for t in first] == [t.id for t in second]
    # no double-apply: balances restored exactly once
    assert c.balance(1001) == 1_000_000
    assert c.balance(3001) == 0


def test_reverse_chain_conservation_guard_rejects_bad_legs():
    c = funded_client()
    with pytest.raises(TransferError):
        reverse_chain(
            c,
            original_idempotency_key="pay-bad",
            legs=[("crf", 3001, 0)],
            original_debit_account=1001,
        )
    with pytest.raises(TransferError):
        reverse_chain(
            c, original_idempotency_key="pay-empty", legs=[], original_debit_account=1001
        )
