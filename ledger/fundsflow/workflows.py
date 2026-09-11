"""Temporal workflow definitions for funds flows (import-guarded temporalio).

When ``temporalio`` is installed the classes are decorated real Temporal
workflows; otherwise an in-process fake runner executes the same step graph
synchronously with retry/backoff/compensation semantics, so tests and local
dev need no Temporal server.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:  # import guard — temporalio is optional
    from temporalio import workflow  # type: ignore

    HAS_TEMPORALIO = True
except Exception:  # pragma: no cover - depends on environment
    workflow = None  # type: ignore
    HAS_TEMPORALIO = False


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    initial_interval: float = 0.001  # seconds
    backoff: float = 2.0


class WorkflowFailure(RuntimeError):
    pass


def run_with_retry(fn: Callable[[], Any], policy: RetryPolicy, clock_sleep=None) -> Any:
    """Exponential-backoff retry used by both Temporal and in-process paths."""
    clock_sleep = clock_sleep or time.sleep
    delay = policy.initial_interval
    last_exc: Optional[Exception] = None
    for attempt in range(policy.max_attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < policy.max_attempts - 1:
                clock_sleep(delay)
                delay *= policy.backoff
    raise WorkflowFailure(f"activity failed after {policy.max_attempts} attempts: {last_exc}")


@dataclass
class FundsTransferWorkflow:
    """hold → split_commit → event_publish → settle, with compensation.

    Steps are callables supplied by the composition root (saga coordinator +
    TB flows + outbox relay), keeping the workflow transport-agnostic.
    """

    hold: Optional[Callable[[], Any]] = None
    split_commit: Optional[Callable[[], Any]] = None
    publish: Optional[Callable[[], Any]] = None
    settle: Optional[Callable[[], Any]] = None
    compensate_hold: Optional[Callable[[], Any]] = None
    compensate_split: Optional[Callable[[], Any]] = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    completed: List[str] = field(default_factory=list)
    compensated: List[str] = field(default_factory=list)

    def run(self) -> Dict[str, Any]:
        # per-step compensation: hold → void pending chain; split_commit →
        # reversal flow; publish/settle → externally final, no compensation
        steps = [
            ("hold", self.hold, self.compensate_hold),
            ("split_commit", self.split_commit, self.compensate_split),
            ("publish", self.publish, None),
            ("settle", self.settle, None),
        ]
        try:
            for name, action, _ in steps:
                if action is not None:
                    run_with_retry(action, self.retry)
                self.completed.append(name)
        except WorkflowFailure:
            for name, _, comp in reversed(steps):
                if name in self.completed and comp is not None:
                    run_with_retry(comp, self.retry)
                    self.compensated.append(name)
            return {"status": "COMPENSATED", "completed": self.completed,
                    "compensated": self.compensated}
        return {"status": "COMPLETED", "completed": self.completed,
                "compensated": self.compensated}


@dataclass
class SettlementReconciliationWorkflow:
    """Scheduled reconciliation sweep (ledger vs outbox vs upstream refs)."""

    reconcile: Optional[Callable[[], Any]] = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    def run(self) -> Any:
        return run_with_retry(self.reconcile or (lambda: None), self.retry)


if HAS_TEMPORALIO:  # pragma: no cover - requires temporalio

    @workflow.defn
    class TemporalFundsTransferWorkflow:  # noqa: D101
        @workflow.run
        async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
            # Activities are registered by the worker; the workflow only
            # orchestrates hold→split→publish→settle with retry + compensation.
            result = await workflow.execute_activity(
                "funds_transfer_saga",
                params,
                start_to_close_timeout=60,
                retry_policy={
                    "maximum_attempts": 5,
                    "initial_interval": 1,
                    "backoff_coefficient": 2.0,
                },
            )
            return result

    @workflow.defn
    class TemporalSettlementReconciliationWorkflow:  # noqa: D101
        @workflow.run
        async def run(self, params: Dict[str, Any]) -> Any:
            return await workflow.execute_activity(
                "settlement_reconciliation_sweep",
                params,
                start_to_close_timeout=300,
                retry_policy={"maximum_attempts": 3},
            )
