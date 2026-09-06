"""Tests for the shared canonical JSON / SHA-256 hash-chain primitives."""

from __future__ import annotations

from hashchain import (
    CHAIN_FIELDS,
    GENESIS_PREV_HASH,
    canonical_json,
    event_payload_hash,
    sha256_hex,
    verify_event_chain,
)


def _event(event_id: str, seq: int, prev_hash: str) -> dict:
    event = {"event_id": event_id, "seq": seq, "amount_kobo": seq * 1000}
    event["prev_hash"] = prev_hash
    event["event_hash"] = event_payload_hash(event, prev_hash)
    return event


def _chain(n: int) -> list[dict]:
    events, prev = [], GENESIS_PREV_HASH
    for i in range(n):
        ev = _event(f"ev-{i}", i, prev)
        events.append(ev)
        prev = ev["event_hash"]
    return events


def test_canonical_json_is_deterministic() -> None:
    a = canonical_json({"b": 1, "a": [2, 3], "c": "x"})
    b = canonical_json({"c": "x", "a": [2, 3], "b": 1})
    assert a == b == '{"a":[2,3],"b":1,"c":"x"}'


def test_sha256_hex_accepts_str_and_bytes() -> None:
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert len(sha256_hex("abc")) == 64


def test_event_payload_hash_strips_chain_fields() -> None:
    payload = {"k": "v", "prev_hash": "ignored", "event_hash": "ignored"}
    clean = {"k": "v"}
    assert CHAIN_FIELDS == ("prev_hash", "event_hash")
    assert event_payload_hash(payload, GENESIS_PREV_HASH) == event_payload_hash(
        clean, GENESIS_PREV_HASH
    )
    # The chain link is folded into the hash.
    assert event_payload_hash(clean, GENESIS_PREV_HASH) != event_payload_hash(clean, "f" * 64)


def test_verify_event_chain_accepts_intact_chain() -> None:
    assert verify_event_chain(_chain(5)) == []


def test_verify_event_chain_detects_tampered_record() -> None:
    events = _chain(3)
    events[1]["amount_kobo"] = 999  # mutation without re-hashing
    errors = verify_event_chain(events)
    assert any("hash mismatch" in e and "ev-1" in e for e in errors)


def test_verify_event_chain_detects_deleted_record() -> None:
    events = _chain(3)
    del events[1]  # deletion breaks prev_hash continuity at the successor
    errors = verify_event_chain(events)
    assert any("broken chain link" in e and "ev-2" in e for e in errors)


def test_verify_event_chain_detects_broken_genesis_link() -> None:
    events = _chain(2)
    events[0]["prev_hash"] = "1" * 64
    errors = verify_event_chain(events)
    assert any("broken chain link" in e and "ev-0" in e for e in errors)


def test_verify_event_chain_labels_unknown_events_by_index() -> None:
    errors = verify_event_chain([{"prev_hash": "bad", "event_hash": "bad"}])
    assert errors and "index-0" in errors[0]


def test_verify_event_chain_empty_is_intact() -> None:
    assert verify_event_chain([]) == []
