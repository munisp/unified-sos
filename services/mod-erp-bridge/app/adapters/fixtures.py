"""Deterministic fixture adapter — the default local/test backend.

Receipts are keyed by the entry hash: the same journal entry always yields
the same receipt, making replays and golden tests stable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from ..domain import ErpBackend, ErpReceipt, JournalEntry

#: Fixed timestamp so fixture receipts are fully deterministic.
FIXTURE_POSTED_AT = datetime(2025, 1, 1, tzinfo=timezone.utc)


class FixtureErpAdapter:
    def __init__(self) -> None:
        self.pushed: List[str] = []  # entry hashes, in push order

    def push_journal(self, entry: JournalEntry, account_map: Dict[str, str]) -> ErpReceipt:
        self.pushed.append(entry.hash or "")
        return ErpReceipt(
            receipt_id=f"FX-{(entry.hash or '')[:16]}",
            backend=ErpBackend.FIXTURE,
            external_ref=f"fixture://journal/{entry.hash}",
            entry_hash=entry.hash or "",
            posted_at=FIXTURE_POSTED_AT,
            detail="deterministic fixture receipt",
        )

    def health(self) -> bool:
        return True
