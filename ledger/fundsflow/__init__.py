"""Funds-flow integrity middleware for the unified-SOS ledger.

Guarantee model (see docs/architecture/funds-flow-integrity.md):

* atomicity     — TigerBeetle PENDING/POST/VOID + linked transfer chains
* idempotency   — deterministic 128-bit transfer IDs + key/hash middleware
* recoverability — saga state machine persisted in a pluggable store
* tamper evidence — hash-chained audit log for every invariant decision
* no event loss — transactional outbox with relay recovery scan

Stdlib-only; optional seams (postgres/redis/temporalio/kafka) are
import-guarded and fail closed in the production profile.
"""

from .idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyMiddleware,
    MONEY_TTL_SECONDS,
    NON_MONEY_TTL_SECONDS,
)
from .invariants import InvariantViolation
from .saga import SagaCoordinator, SagaState
from .tigerbeetle_flows import (
    InMemoryTBClient,
    deterministic_transfer_id,
    reverse_chain,
)

__all__ = [
    "IdempotencyConflict",
    "IdempotencyInProgress",
    "IdempotencyMiddleware",
    "MONEY_TTL_SECONDS",
    "NON_MONEY_TTL_SECONDS",
    "InvariantViolation",
    "SagaCoordinator",
    "SagaState",
    "InMemoryTBClient",
    "deterministic_transfer_id",
    "reverse_chain",
]
