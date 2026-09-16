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


# ---------------------------------------------------------------------------
# Real, registry-backed funds-flow steps.
#
# Every step is described at ``begin()`` by a JSON-serializable descriptor
# (adapter name, account ids, leg amounts) persisted in SagaRecord.context
# under "step_descriptors". ``recover_unfinished`` rebuilds executable steps
# from STEP_REGISTRY, so recovery works after a full process restart with no
# closures carried over. Compensations are REAL money movements:
#   hold         → void the pending chain
#   split_commit → reverse_chain of the posted legs
#   payout       → reverse the payout transfer
# An unbound compensation is impossible: a descriptor whose kind is not in
# the registry raises at build time (fail-closed).
# ---------------------------------------------------------------------------

SAGA_FAILED_TOPIC = "ng.sos.fundsflow.saga_failed"

# descriptor kind → builder(descriptor: dict, deps: dict) -> SagaStep
STEP_REGISTRY: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], SagaStep]] = {}


def register_step(kind: str):
    def deco(builder):
        STEP_REGISTRY[kind] = builder
        return builder
    return deco


def canonical_step_descriptors(
    *,
    source_account: int,
    legs: List[tuple],  # [(leg_name, destination_account, amount_kobo), ...]
    payout: Optional[tuple] = None,  # (payout_account, destination_account, amount_kobo)
    event_topic: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """JSON-serializable descriptors for the canonical funds-flow saga."""
    leg_dicts = [
        {"leg": leg, "destination_account": dest, "amount_kobo": int(amount)}
        for (leg, dest, amount) in legs
    ]
    descriptors: List[Dict[str, Any]] = [
        {"kind": "tigerbeetle.hold", "adapter": "tigerbeetle",
         "source_account": source_account, "legs": leg_dicts},
        {"kind": "tigerbeetle.split_commit", "adapter": "tigerbeetle",
         "source_account": source_account, "legs": leg_dicts},
    ]
    if payout is not None:
        pay_acct, pay_dest, pay_amount = payout
        descriptors.append(
            {"kind": "tigerbeetle.payout", "adapter": "tigerbeetle",
             "payout_account": pay_acct, "destination_account": pay_dest,
             "amount_kobo": int(pay_amount)}
        )
    descriptors.append(
        {"kind": "outbox.event_publish", "adapter": "outbox",
         "topic": event_topic or "ng.sos.fundsflow.payment_completed"}
    )
    return descriptors


def _desc_legs(desc: Dict[str, Any]) -> List[tuple]:
    return [
        (l["leg"], int(l["destination_account"]), int(l["amount_kobo"]))
        for l in desc["legs"]
    ]


@register_step("tigerbeetle.hold")
def _build_hold_step(desc, deps):
    from .tigerbeetle_flows import build_hold_chain

    def action(ctx: SagaContext) -> None:
        chain = build_hold_chain(
            idempotency_key=ctx.idempotency_key,
            source_account=int(desc["source_account"]),
            legs=_desc_legs(desc),
        )
        deps["tb_client"].create_transfers(chain)
        ctx.data["hold_chain_ids"] = [t.id for t in chain]

    def compensate(ctx: SagaContext) -> None:
        # Void only legs that are still PENDING. If split_commit already
        # posted the chain, its own compensation (reverse_chain) has run
        # first in the reverse-order walk; voiding posted transfers is
        # illegal and would fail the whole compensation.
        client = deps["tb_client"]
        ids = [int(i) for i in ctx.data["hold_chain_ids"]]
        if hasattr(client, "transfers"):
            ids = [
                i for i in ids
                if getattr(client.transfers.get(i), "state", "PENDING") == "PENDING"
            ]
        if ids:
            client.void_pending_transfers(ids)

    return SagaStep("hold", action, compensate, SagaState.HELD)


@register_step("tigerbeetle.split_commit")
def _build_split_commit_step(desc, deps):
    from .tigerbeetle_flows import reverse_chain

    def action(ctx: SagaContext) -> None:
        deps["tb_client"].post_pending_transfers(
            [int(i) for i in ctx.data["hold_chain_ids"]]
        )

    def compensate(ctx: SagaContext) -> None:
        # the chain is POSTED — voiding is illegal; reverse with swapped accounts
        reverse_chain(
            deps["tb_client"],
            original_idempotency_key=ctx.idempotency_key,
            legs=_desc_legs(desc),
            original_debit_account=int(desc["source_account"]),
        )

    return SagaStep("split_commit", action, compensate, SagaState.SPLIT_COMMITTED)


@register_step("tigerbeetle.payout")
def _build_payout_step(desc, deps):
    from .tigerbeetle_flows import Transfer, deterministic_transfer_id

    def action(ctx: SagaContext) -> None:
        t = Transfer(
            id=deterministic_transfer_id(ctx.idempotency_key, "payout"),
            debit_account=int(desc["payout_account"]),
            credit_account=int(desc["destination_account"]),
            amount=int(desc["amount_kobo"]),
            pending=False,
            idempotency_key=ctx.idempotency_key,
            leg="payout",
        )
        deps["tb_client"].create_transfers([t])
        ctx.data["payout_transfer_id"] = t.id

    def compensate(ctx: SagaContext) -> None:
        # payout settled instantly — compensate with a reversal transfer
        t = Transfer(
            id=deterministic_transfer_id(ctx.idempotency_key, "payout|reversal"),
            debit_account=int(desc["destination_account"]),
            credit_account=int(desc["payout_account"]),
            amount=int(desc["amount_kobo"]),
            pending=False,
            idempotency_key=f"{ctx.idempotency_key}|reversal",
            leg="payout|reversal",
        )
        deps["tb_client"].create_transfers([t])

    return SagaStep("payout", action, compensate, SagaState.PAYOUT_DONE)


@register_step("outbox.event_publish")
def _build_event_publish_step(desc, deps):
    def action(ctx: SagaContext) -> None:
        writer = deps.get("outbox_writer")
        if writer is None:
            raise AdapterUnavailableError(
                "outbox.event_publish requires deps['outbox_writer'] (fail-closed)"
            )
        writer.write(
            domain_record={"saga_id": ctx.saga_id, "step": "event_publish"},
            topic=desc["topic"],
            event_payload={"saga_id": ctx.saga_id,
                           "idempotency_key": ctx.idempotency_key},
            idempotency_key=f"{ctx.idempotency_key}|event_publish",
        )

    return SagaStep("event_publish", action, None, SagaState.EVENT_PUBLISHED)


def build_steps_from_descriptors(
    descriptors: List[Dict[str, Any]], deps: Dict[str, Any]
) -> List[SagaStep]:
    """Rebuild executable steps from persisted descriptors via the registry."""
    steps = []
    for desc in descriptors:
        builder = STEP_REGISTRY.get(desc.get("kind"))
        if builder is None:
            raise AdapterUnavailableError(
                f"no registered builder for step kind {desc.get('kind')!r} "
                "(fail-closed: unbound compensation is impossible)"
            )
        steps.append(builder(desc, deps))
    return steps


class SagaCoordinator:
    """Drives a saga to a terminal state with persisted transitions."""

    def __init__(
        self,
        store: Optional[SagaStore] = None,
        outbox_writer: Optional[Any] = None,
        deps: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.store = store or InMemorySagaStore()
        # outbox writer used to publish ng.sos.fundsflow.saga_failed on FAILED
        self.outbox_writer = outbox_writer
        # adapter deps (tb_client, outbox_writer, ...) for registry rebuilds
        self.deps: Dict[str, Any] = dict(deps or {})

    def begin(
        self,
        idempotency_key: str,
        data: Optional[Dict[str, Any]] = None,
        step_descriptors: Optional[List[Dict[str, Any]]] = None,
    ) -> SagaRecord:
        existing = self.store.by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing  # idempotent begin: replay returns the same saga
        context = dict(data or {})
        if step_descriptors is not None:
            # persisted at begin: JSON-serializable action descriptors so
            # recovery after a process restart can rebuild real steps
            context["step_descriptors"] = step_descriptors
        record = SagaRecord(
            saga_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            state=SagaState.INIT,
            context=context,
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
            self._emit_failed(record, error=str(exc))
            return record
        record.state = SagaState.COMPENSATED
        self.store.save(record)
        return record

    def _emit_failed(self, record: SagaRecord, *, error: str) -> None:
        """Publish ng.sos.fundsflow.saga_failed via the transactional outbox."""
        if self.outbox_writer is None:
            return  # no outbox configured (tests/dev); state is still FAILED
        step = record.completed_steps[-1] if record.completed_steps else None
        self.outbox_writer.write(
            domain_record={"saga_id": record.saga_id, "state": SagaState.FAILED.value},
            topic=SAGA_FAILED_TOPIC,
            event_payload={
                "saga_id": record.saga_id,
                "step": step,
                "error": error,
            },
            idempotency_key=f"saga_failed|{record.saga_id}",
        )

    def _steps_for(self, record: SagaRecord, steps: Optional[List[SagaStep]]) -> List[SagaStep]:
        if steps is not None:
            return steps
        descriptors = record.context.get("step_descriptors")
        if not descriptors:
            raise AdapterUnavailableError(
                "recover_unfinished requires steps or persisted step_descriptors"
            )
        return build_steps_from_descriptors(descriptors, self.deps)

    def recover_unfinished(
        self, steps: Optional[List[SagaStep]] = None
    ) -> List[SagaRecord]:
        """Recovery pass: drive every persisted non-terminal saga forward.

        When ``steps`` is omitted, executable steps are rebuilt from the
        descriptors persisted in each record's context via STEP_REGISTRY
        using this coordinator's ``deps`` — recovery works after a full
        process restart.
        """
        recovered = []
        for record in self.store.unfinished():
            resolved = self._steps_for(record, steps)
            if record.state in (SagaState.COMPENSATING,):
                ctx = SagaContext(record.saga_id, record.idempotency_key, record.context)
                recovered.append(self._compensate(record, resolved, ctx))
            else:
                recovered.append(self.execute(record, resolved))
        return recovered
