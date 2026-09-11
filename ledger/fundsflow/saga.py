"""Funds-flow saga coordinator.

Models every money movement as an atomic saga:

    payment received → HOLD (pending TB chain)
                     → SPLIT_COMMIT (POST pending chain)
                     → PAYOUT (concessionaire escrow / settlement payout)
                     → EVENT_PUBLISH (transactional outbox)

Every step has a compensation. On failure the coordinator walks
compensations in reverse order (VOID pending chain, reversal payout, …).
Saga state is persisted after every transition via a pluggable store so a
crashed saga can be recovered and driven to a terminal state
(COMPLETED or COMPENSATED) — never left half-applied.

Stores: in-memory default; Postgres seam via ``SOS_FUNDSFLOW_DSN``
(fail-closed: the seam raises unless the DSN and driver are present).
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol


class AdapterUnavailableError(RuntimeError):
    pass


class SagaState(str, Enum):
    INIT = "INIT"
    HELD = "HELD"
    SPLIT_COMMITTED = "SPLIT_COMMITTED"
    PAYOUT_DONE = "PAYOUT_DONE"
    EVENT_PUBLISHED = "EVENT_PUBLISHED"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"  # compensation itself failed — operator alert


TERMINAL_STATES = {SagaState.COMPLETED, SagaState.COMPENSATED, SagaState.FAILED}


@dataclass
class SagaStep:
    name: str
    action: Callable[["SagaContext"], None]
    compensate: Optional[Callable[["SagaContext"], None]] = None
    forward_state: Optional[SagaState] = None


@dataclass
class SagaContext:
    saga_id: str
    idempotency_key: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SagaRecord:
    saga_id: str
    idempotency_key: str
    state: SagaState
    completed_steps: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


class SagaStore(Protocol):
    def save(self, record: SagaRecord) -> None: ...

    def load(self, saga_id: str) -> Optional[SagaRecord]: ...

    def by_idempotency_key(self, key: str) -> Optional[SagaRecord]: ...

    def unfinished(self) -> List[SagaRecord]: ...


class InMemorySagaStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._records: Dict[str, SagaRecord] = {}
        self._clock = clock
        self.crash_on_save_state: Optional[SagaState] = None  # test hook

    def save(self, record: SagaRecord) -> None:
        if self.crash_on_save_state is record.state:
            raise RuntimeError(f"injected crash saving state {record.state}")
        record.updated_at = self._clock()
        self._records[record.saga_id] = record

    def load(self, saga_id: str) -> Optional[SagaRecord]:
        return self._records.get(saga_id)

    def by_idempotency_key(self, key: str) -> Optional[SagaRecord]:
        for r in self._records.values():
            if r.idempotency_key == key:
                return r
        return None

    def unfinished(self) -> List[SagaRecord]:
        return [r for r in self._records.values() if r.state not in TERMINAL_STATES]


class PostgresSagaStore:
    """Fail-closed Postgres seam (SOS_FUNDSFLOW_DSN). Reference seam only —
    raises unless DSN + psycopg are present; DDL lives in db/."""

    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or os.environ.get("SOS_FUNDSFLOW_DSN")
        if not self.dsn:
            raise AdapterUnavailableError(
                "Postgres saga store requires SOS_FUNDSFLOW_DSN (fail-closed)"
            )
        try:  # pragma: no cover - optional dependency
            import psycopg  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise AdapterUnavailableError("psycopg package not installed") from exc


def default_steps() -> List[SagaStep]:
    """Step names for the canonical funds-flow saga (actions bound by caller)."""
    return [
        SagaStep("hold", lambda ctx: None, lambda ctx: None, SagaState.HELD),
        SagaStep("split_commit", lambda ctx: None, lambda ctx: None,
                 SagaState.SPLIT_COMMITTED),
        SagaStep("payout", lambda ctx: None, lambda ctx: None, SagaState.PAYOUT_DONE),
        SagaStep("event_publish", lambda ctx: None, None, SagaState.EVENT_PUBLISHED),
    ]


class SagaCoordinator:
    """Drives a saga to a terminal state with persisted transitions."""

    def __init__(self, store: Optional[SagaStore] = None) -> None:
        self.store = store or InMemorySagaStore()

    def begin(self, idempotency_key: str, data: Optional[Dict[str, Any]] = None) -> SagaRecord:
        existing = self.store.by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing  # idempotent begin: replay returns the same saga
        record = SagaRecord(
            saga_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            state=SagaState.INIT,
            context=dict(data or {}),
        )
        self.store.save(record)
        return record

    def execute(self, record: SagaRecord, steps: List[SagaStep]) -> SagaRecord:
        ctx = SagaContext(record.saga_id, record.idempotency_key, record.context)
        if record.state in TERMINAL_STATES:
            return record
        # resume: skip steps already completed (crash recovery)
        start = len(record.completed_steps)
        for step in steps[start:]:
            try:
                step.action(ctx)
            except Exception as exc:
                record.context["error"] = str(exc)
                return self._compensate(record, steps, ctx)
            record.completed_steps.append(step.name)
            if step.forward_state is not None:
                record.state = step.forward_state
            self.store.save(record)
        record.state = SagaState.COMPLETED
        self.store.save(record)
        return record

    def _compensate(
        self, record: SagaRecord, steps: List[SagaStep], ctx: SagaContext
    ) -> SagaRecord:
        record.state = SagaState.COMPENSATING
        self.store.save(record)
        by_name = {s.name: s for s in steps}
        try:
            for step_name in reversed(record.completed_steps):
                comp = by_name[step_name].compensate
                if comp is not None:
                    comp(ctx)
        except Exception as exc:
            record.context["compensation_error"] = str(exc)
            record.state = SagaState.FAILED
            self.store.save(record)
            return record
        record.state = SagaState.COMPENSATED
        self.store.save(record)
        return record

    def recover_unfinished(self, steps: List[SagaStep]) -> List[SagaRecord]:
        """Recovery pass: drive every persisted non-terminal saga forward."""
        recovered = []
        for record in self.store.unfinished():
            if record.state in (SagaState.COMPENSATING,):
                ctx = SagaContext(record.saga_id, record.idempotency_key, record.context)
                recovered.append(self._compensate(record, steps, ctx))
            else:
                recovered.append(self.execute(record, steps))
        return recovered
