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
