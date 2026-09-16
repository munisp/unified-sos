"""End-of-month sweep: execute accumulated month-end legs (ledger/splits Go).

``ledger/splits`` (Go) computes ``MonthEndLegs`` and a ``ClearingRemainder``
per clearing cycle, but computing them does not move money. This module is
the execution half: it sweeps the accumulated EOM legs out of the payer
clearing account as ONE linked chain (all-or-nothing), then reconciles the
clearing remainder to zero with a final remainder leg to the CRF account.

Idempotency: the run key is ``EOM|{tenant}|{YYYY-MM}`` and every transfer ID
is derived via :func:`deterministic_transfer_id`, so a double-run or a
retried run maps onto the same IDs and is a no-op (TigerBeetle idempotent
create). The injected ``store`` records completed runs as a second
fail-closed guard: a run recorded COMPLETED returns without touching the
ledger.

Temporal-ready: ``run_eom_sweep`` is a pure orchestration function — no
global state, injected ``clock``, all effects through the injected client
and store. In a Temporal deployment this body is a workflow whose
activities are the client calls; the deterministic run key becomes the
workflow ID (``EOM|tenant|YYYY-MM``) so Temporal's workflow-ID dedupe gives
the same exactly-once guarantee.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Tuple

from .tigerbeetle_flows import (
    Transfer,
    TransferError,
    build_hold_chain,
    deterministic_transfer_id,
)

CRF_ACCOUNT = 3001  # Consolidated Revenue Fund ledger account (house default)


class EomSweepError(RuntimeError):
    """Raised on conservation or execution failure (fail-closed)."""


def eom_run_key(tenant: str, year_month: str) -> str:
    """Deterministic run key, e.g. EOM|lasg|2025-01 (doubles as workflow ID)."""
    return f"EOM|{tenant}|{year_month}"


def run_eom_sweep(
    client: Any,
    *,
    tenant: str,
    year_month: str,  # "YYYY-MM"
    clearing_account: int,
    legs: List[Tuple[str, int, int]],  # [(leg_name, dest_account, amount_kobo)]
    crf_account: int = CRF_ACCOUNT,
    store: Optional[MutableMapping[str, Dict[str, Any]]] = None,
    clock: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Execute the accumulated EOM legs for ``tenant``/``year_month``.

    Returns a run report. Double-runs/retries are no-ops: they return the
    recorded report (store-backed) or re-resolve to the same deterministic
    transfer IDs (client-idempotent) without double-applying.
    """
    run_key = eom_run_key(tenant, year_month)
    if store is not None:
        prior = store.get(run_key)
        if prior is not None and prior.get("status") == "COMPLETED":
            return {**prior, "replayed": True}  # double-run is a no-op

    if not legs:
        raise EomSweepError(f"{run_key}: nothing to sweep (no EOM legs)")
    if any(amount <= 0 for _, _, amount in legs):
        raise EomSweepError(f"{run_key}: EOM leg amounts must be positive kobo")

    # 1) Execute the accumulated EOM legs as ONE linked chain: pending hold
    #    out of the clearing account, then POST (all-or-nothing).
    chain = build_hold_chain(
        idempotency_key=run_key,
        source_account=clearing_account,
        legs=list(legs),
    )
    created = client.create_transfers(chain)
    client.post_pending_transfers([t.id for t in created])
    swept_total = sum(t.amount for t in created)

    # 2) Reconcile the clearing remainder to zero: whatever is left in the
    #    clearing account after the sweep (the Go plan's ClearingRemainder)
    #    moves to the CRF account as a deterministic remainder leg.
    remainder = client.balance(clearing_account)
    remainder_transfer_id: Optional[int] = None
    if remainder != 0:
        if remainder < 0:
            # fail-closed: the clearing account is overdrawn — conservation
            # has already been violated upstream; do not paper over it
            raise EomSweepError(
                f"{run_key}: clearing account {clearing_account} overdrawn "
                f"by {-remainder} kobo after sweep"
            )
        remainder_leg = Transfer(
            id=deterministic_transfer_id(run_key, "clearing_remainder|crf"),
            debit_account=clearing_account,
            credit_account=crf_account,
            amount=remainder,
            pending=False,
            idempotency_key=run_key,
            leg="clearing_remainder|crf",
        )
        client.create_transfers([remainder_leg])
        remainder_transfer_id = remainder_leg.id

    if client.balance(clearing_account) != 0:
        raise EomSweepError(
            f"{run_key}: clearing account {clearing_account} not zero after sweep"
        )

    report: Dict[str, Any] = {
        "run_key": run_key,
        "tenant": tenant,
        "year_month": year_month,
        "status": "COMPLETED",
        "swept_total_kobo": swept_total,
        "remainder_kobo": remainder if remainder > 0 else 0,
        "transfer_ids": [t.id for t in created],
        "remainder_transfer_id": remainder_transfer_id,
        "completed_at": clock(),
        "replayed": False,
    }
    if store is not None:
        store[run_key] = report
    return report
