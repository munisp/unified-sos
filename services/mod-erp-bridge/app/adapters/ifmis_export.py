"""GIFMIS/IFMIS-style journal export adapter.

Writes a deterministic CSV + JSON export artifact per journal entry into a
local output directory (mounted volume in deployment), COA-mapped, suitable
for ingestion into a government IFMIS. Always available in local mode —
this is the offline/sovereign fallback backend.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Dict, Optional

from ..domain import ErpBackend, ErpReceipt, JournalEntry, canonical_json
from .base import ErpPushError

CSV_HEADER = (
    "entry_id",
    "tenant_state_id",
    "date",
    "memo",
    "erp_account",
    "debit_kobo",
    "credit_kobo",
    "source_event_id",
    "entry_hash",
)


class IfmisExportAdapter:
    """Deterministic file-based export; always available."""

    def __init__(self, out_dir: Optional[str] = None) -> None:
        self.out_dir = Path(
            out_dir or os.environ.get("IFMIS_EXPORT_DIR", "/tmp/ifmis-export")
        )

    def _render(self, entry: JournalEntry, account_map: Dict[str, str]) -> tuple[str, str]:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for line in entry.lines:
            writer.writerow(
                (
                    entry.entry_id,
                    entry.tenant_state_id,
                    entry.date.isoformat(),
                    entry.memo,
                    account_map.get(line.account_code, line.account_code),
                    line.debit_kobo,
                    line.credit_kobo,
                    entry.source_event_id,
                    entry.hash,
                )
            )
        payload = {
            "entry_id": entry.entry_id,
            "tenant_state_id": entry.tenant_state_id,
            "date": entry.date.isoformat(),
            "memo": entry.memo,
            "source_event_id": entry.source_event_id,
            "entry_hash": entry.hash,
            "lines": [
                {
                    "erp_account": account_map.get(l.account_code, l.account_code),
                    "debit_kobo": l.debit_kobo,
                    "credit_kobo": l.credit_kobo,
                }
                for l in entry.lines
            ],
        }
        return buf.getvalue(), json.dumps(payload, sort_keys=True, indent=2) + "\n"

    def push_journal(self, entry: JournalEntry, account_map: Dict[str, str]) -> ErpReceipt:
        try:
            target = self.out_dir / entry.tenant_state_id
            target.mkdir(parents=True, exist_ok=True)
            csv_text, json_text = self._render(entry, account_map)
            stem = f"{entry.date.isoformat()}_{entry.entry_id}"
            (target / f"{stem}.csv").write_text(csv_text)
            (target / f"{stem}.json").write_text(json_text)
        except OSError as exc:
            raise ErpPushError(f"ifmis export write failed: {exc}") from exc
        return ErpReceipt(
            receipt_id=f"IFMIS-{entry.hash[:16]}",
            backend=ErpBackend.IFMIS_EXPORT,
            external_ref=str(target / f"{stem}.csv"),
            entry_hash=entry.hash or "",
            detail="exported deterministic CSV+JSON journal artifact",
        )

    def health(self) -> bool:
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            return os.access(self.out_dir, os.W_OK)
        except OSError:
            return False


# canonical_json re-exported for export consumers that hash artifacts.
__all__ = ["IfmisExportAdapter", "canonical_json"]
