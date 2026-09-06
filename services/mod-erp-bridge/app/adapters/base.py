"""Adapter base types for mod-erp-bridge (fail-closed adapter idiom)."""
from __future__ import annotations

from typing import Dict, Protocol

from ..domain import ErpReceipt, JournalEntry


class AdapterUnavailableError(RuntimeError):
    """Raised when a production ERP adapter is selected without configuration."""


class ErpPushError(RuntimeError):
    """Raised when the ERP backend rejects or fails a journal push."""


class ErpAdapter(Protocol):
    """Backend seam. Implementations must be tenant-agnostic; the service
    layer applies COA mapping before calling :meth:`push_journal`."""

    def push_journal(
        self, entry: JournalEntry, account_map: Dict[str, str]
    ) -> ErpReceipt: ...

    def health(self) -> bool: ...
