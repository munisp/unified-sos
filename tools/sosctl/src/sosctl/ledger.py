"""Chart-of-accounts bootstrap for a state tenant (Days 31-60 playbook).

Emits deterministic JSON mapping the state's Consolidated Revenue Fund (CRF)
onto the TigerBeetle chart of accounts (``ledger/chart-of-accounts.md``),
keyed by the gazetted statutory split order.
"""

from __future__ import annotations

import json
from pathlib import Path

from .states import DEFAULT_CHART_OF_ACCOUNTS, STATE_TENANT_IDS, TRANSFER_CODES

#: NG State Sovereign Ledger ID (ledger/chart-of-accounts.md).
LEDGER_ID = 1


def build_chart(state: str, gazette_ref: str) -> dict:
    """Build the chart-of-accounts bootstrap document for a state tenant."""
    if state not in STATE_TENANT_IDS:
        raise ValueError(
            f"unknown state '{state}'; valid: {', '.join(sorted(STATE_TENANT_IDS))}"
        )
    if not gazette_ref or not gazette_ref.strip():
        raise ValueError("gazette reference is required (90-day playbook, Days 1-30)")
    tenant_id = STATE_TENANT_IDS[state]
    return {
        "tenant_state_id": state,
        "ledger_id": LEDGER_ID,
        "tigerbeetle_tenant_partition": tenant_id,
        "gazette_reference": gazette_ref.strip(),
        "account_id_layout": "bits[0..15]=tenant bits[16..31]=mda_category "
        "bits[32..47]=account_class bits[48..127]=entity",
        "accounts": [
            {
                "code": entry["code"],
                "name": entry["name"],
                "account_class": entry["class"],
                # 128-bit identifier: tenant in bits 0..15, class in bits 32..47.
                "account_id_seed": (int(entry["code"]) << 32) | tenant_id,
            }
            for entry in DEFAULT_CHART_OF_ACCOUNTS
        ],
        "transfer_codes": [
            {"code": code, "meaning": meaning}
            for code, meaning in sorted(TRANSFER_CODES.items())
        ],
        "notes": [
            "Splits execute as linked atomic transfer chains (Flags.Linked).",
            "Federal Royalty Pass-Through (5001) is segregated by construction and "
            "is never credited to state revenue splits.",
        ],
    }


def write_chart(state: str, gazette_ref: str, out_dir: Path) -> Path:
    """Write the bootstrap JSON. Idempotent: deterministic content."""
    dest_dir = Path(out_dir) / state
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "chart-of-accounts.json"
    content = json.dumps(build_chart(state, gazette_ref), indent=2, sort_keys=True) + "\n"
    if not (dest.exists() and dest.read_text() == content):
        dest.write_text(content)
    return dest


def build_account_plan(state: str, gazette_ref: str = "sosctl-ledger-init") -> list[dict]:
    """Build the deterministic account-creation plan for a state tenant.

    One entry per chart-of-accounts class; IDs are the 128-bit seeds from
    the chart bootstrap (tenant in bits 0..15, class in bits 32..47), so
    re-running the plan is idempotent.
    """
    chart = build_chart(state, gazette_ref)
    return [
        {
            "account_id": acct["account_id_seed"],
            "code": acct["code"],
            "name": acct["name"],
            "ledger_id": chart["ledger_id"],
        }
        for acct in chart["accounts"]
    ]


def provision_accounts(
    state: str,
    addresses: list[str],
    cluster_id: int = LEDGER_ID,
    gazette_ref: str = "sosctl-ledger-init",
) -> dict:
    """Create the state's chart-of-accounts accounts on a live cluster.

    Idempotent: TigerBeetle answers ``exists`` for already-created
    accounts, which is treated as success. FAIL-CLOSED: raises
    RuntimeError when no addresses are supplied or the tigerbeetle
    Python client is unavailable — never silently no-ops.
    """
    if not addresses:
        raise RuntimeError(
            "no TigerBeetle addresses supplied; set TB_ADDRESSES or pass --addresses"
        )
    try:
        import tigerbeetle as tb  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "tigerbeetle Python client not installed; run the Go adapter "
            "(`-tags tigerbeetle`) path or install tigerbeetle-client"
        ) from exc

    plan = build_account_plan(state, gazette_ref)
    created, existing = 0, 0
    with tb.Client(cluster_id=cluster_id, replica_addresses=addresses) as client:
        results = client.create_accounts(
            [
                tb.Account(
                    id=acct["account_id"],
                    ledger=acct["ledger_id"],
                    code=acct["code"],
                )
                for acct in plan
            ]
        )
        by_index = {r.index: r for r in results}
        for i in range(len(plan)):
            outcome = by_index.get(i)
            if outcome is None:
                created += 1  # no result entry = committed
            elif str(outcome.result).endswith("EXISTS"):
                existing += 1
            else:
                raise RuntimeError(
                    f"account {plan[i]['code']} ({plan[i]['name']}) rejected: {outcome.result}"
                )
    return {
        "state": state,
        "cluster_id": cluster_id,
        "addresses": addresses,
        "accounts_planned": len(plan),
        "accounts_created": created,
        "accounts_already_existing": existing,
    }
