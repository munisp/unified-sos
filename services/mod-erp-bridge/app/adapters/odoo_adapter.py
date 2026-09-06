"""Odoo XML-RPC/JSON-RPC adapter (stdlib only — no extra dependencies).

Fail-closed: selecting ``ERP_BACKEND=odoo`` without the full ODOO_*
environment raises :class:`AdapterUnavailableError` at boot. Network errors
and Odoo faults raise :class:`ErpPushError` so the service layer can retry
and dead-letter.
"""
from __future__ import annotations

import os
import xmlrpc.client
from typing import Callable, Dict, Optional

from ..domain import ErpBackend, ErpReceipt, JournalEntry
from .base import AdapterUnavailableError, ErpPushError

REQUIRED_ENV = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")


class OdooAdapter:
    """Posts each journal entry as an ``account.move`` with line items."""

    def __init__(
        self,
        url: str,
        db: str,
        user: str,
        api_key: str,
        server_proxy_factory: Optional[Callable[[str], object]] = None,
    ) -> None:
        self.url = url
        self.db = db
        self.user = user
        self.api_key = api_key
        # Injectable for tests (stubbed ServerProxy / fake transport).
        self._factory = server_proxy_factory or xmlrpc.client.ServerProxy

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "OdooAdapter":
        env = dict(os.environ if env is None else env)
        missing = [k for k in REQUIRED_ENV if not env.get(k)]
        if missing:
            raise AdapterUnavailableError(
                "ERP_BACKEND=odoo but missing required env vars: "
                + ", ".join(missing)
                + " (fail-closed: refusing to start an unconfigured production adapter)"
            )
        return cls(
            url=env["ODOO_URL"],
            db=env["ODOO_DB"],
            user=env["ODOO_USER"],
            api_key=env["ODOO_API_KEY"],
        )

    def _uid(self) -> int:
        try:
            common = self._factory(f"{self.url}/xmlrpc/2/common")
            uid = common.authenticate(self.db, self.user, self.api_key, {})
        except Exception as exc:  # noqa: BLE001 — any transport fault is a push failure
            raise ErpPushError(f"odoo authenticate transport error: {exc}") from exc
        if not uid:
            raise ErpPushError("odoo authentication failed (check ODOO_USER/ODOO_API_KEY)")
        return int(uid)

    def push_journal(self, entry: JournalEntry, account_map: Dict[str, str]) -> ErpReceipt:
        uid = self._uid()
        lines = [
            (
                0,
                0,
                {
                    "account_code": account_map.get(l.account_code, l.account_code),
                    "name": entry.memo or entry.entry_id,
                    "debit": l.debit_kobo / 100.0,
                    "credit": l.credit_kobo / 100.0,
                },
            )
            for l in entry.lines
        ]
        move_vals = {
            "move_type": "entry",
            "ref": entry.source_event_id,
            "date": entry.date.isoformat(),
            "journal_entry_hash": entry.hash,
            "line_ids": lines,
        }
        try:
            models = self._factory(f"{self.url}/xmlrpc/2/object")
            move_id = models.execute_kw(
                self.db, uid, self.api_key, "account.move", "create", [move_vals]
            )
            models.execute_kw(
                self.db, uid, self.api_key, "account.move", "action_post", [[move_id]]
            )
        except Exception as exc:  # noqa: BLE001
            raise ErpPushError(f"odoo account.move push failed: {exc}") from exc
        return ErpReceipt(
            receipt_id=f"ODOO-{move_id}",
            backend=ErpBackend.ODOO,
            external_ref=str(move_id),
            entry_hash=entry.hash or "",
            detail=f"account.move {move_id} posted",
        )

    def health(self) -> bool:
        try:
            self._uid()
            return True
        except ErpPushError:
            return False
