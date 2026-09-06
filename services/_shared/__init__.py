"""Shared hash-chain helpers (P1 audit immutability).

Canonical JSON + SHA-256 primitives used by the control-plane audit log,
mod-geospatial geometry hashing, and the sosctl verification CLI. Pure
stdlib; importable from any service by adding the ``services/`` directory
to ``sys.path`` (each service ships its own ``app`` package).
"""

from .hashchain import (
    GENESIS_PREV_HASH,
    canonical_json,
    event_payload_hash,
    sha256_hex,
    verify_event_chain,
)

__all__ = [
    "GENESIS_PREV_HASH",
    "canonical_json",
    "sha256_hex",
    "event_payload_hash",
    "verify_event_chain",
]
