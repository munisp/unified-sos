"""Canonical JSON / SHA-256 hash-chain primitives (P1 audit immutability).

A hash chain is a sequence of records where each record carries:

  * ``prev_hash``  — the ``event_hash`` of the previous record in the chain
                     (``GENESIS_PREV_HASH`` for the first record), and
  * ``event_hash`` — SHA-256 over the canonical JSON of the record payload
                     plus ``prev_hash``.

Any mutation, deletion, or re-ordering of archived records breaks the chain
and is detected by :func:`verify_event_chain`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

#: ``prev_hash`` of the first (genesis) record in every chain.
GENESIS_PREV_HASH = "0" * 64

#: Fields managed by the chain itself — excluded from the hashed payload.
CHAIN_FIELDS = ("prev_hash", "event_hash")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization used for all hashing."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def event_payload_hash(payload: Mapping[str, Any], prev_hash: str) -> str:
    """Hash one record's payload chained to ``prev_hash``.

    ``payload`` must not contain ``prev_hash``/``event_hash`` (they are
    stripped defensively); the chain link is folded in explicitly.
    """

    body = {k: v for k, v in payload.items() if k not in CHAIN_FIELDS}
    body["prev_hash"] = prev_hash
    return sha256_hex(canonical_json(body))


def verify_event_chain(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Recompute a hash chain over ``events`` (in order).

    Returns a list of human-readable error strings; empty means the chain
    is intact. Checks genesis linkage, per-record hash recomputation, and
    ``prev_hash`` continuity.
    """

    errors: list[str] = []
    last_hash: str | None = None
    for index, event in enumerate(events):
        label = event.get("event_id", f"index-{index}")
        prev = event.get("prev_hash")
        expected_prev = GENESIS_PREV_HASH if last_hash is None else last_hash
        if prev != expected_prev:
            errors.append(
                f"event {label}: broken chain link (prev_hash={prev!r}, "
                f"expected {expected_prev!r})"
            )
        recorded = event.get("event_hash")
        recomputed = event_payload_hash(event, prev if prev is not None else "")
        if recorded != recomputed:
            errors.append(
                f"event {label}: hash mismatch (recorded={recorded!r}, "
                f"recomputed={recomputed!r}) — record tampered"
            )
        last_hash = recorded
    return errors
