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

from .idempotency import IdempotencyConflict, IdempotencyMiddleware
from .invariants import InvariantViolation
from .saga import SagaCoordinator, SagaState
from .tigerbeetle_flows import InMemoryTBClient, deterministic_transfer_id

__all__ = [
    "IdempotencyConflict",
    "IdempotencyMiddleware",
    "InvariantViolation",
    "SagaCoordinator",
    "SagaState",
    "InMemoryTBClient",
    "deterministic_transfer_id",
]
