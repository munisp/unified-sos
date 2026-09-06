"""ERPNext REST adapter (httpx).

Fail-closed: selecting ``ERP_BACKEND=erpnext`` without the full ERPNEXT_*
environment raises :class:`AdapterUnavailableError` at boot. HTTP errors and
non-2xx responses raise :class:`ErpPushError` for retry / dead-letter.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import httpx

from ..domain import ErpBackend, ErpReceipt, JournalEntry
from .base import AdapterUnavailableError, ErpPushError

REQUIRED_ENV = ("ERPNEXT_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET")


class ErpNextAdapter:
    """Posts each journal entry as an ERPNext ``Journal Entry`` document."""

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        # Injectable for tests (httpx.MockTransport-backed client).
        self._client = client or httpx.Client(
            base_url=self.url,
            headers={
                "Authorization": f"token {api_key}:{api_secret}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ErpNextAdapter":
        env = dict(os.environ if env is None else env)
        missing = [k for k in REQUIRED_ENV if not env.get(k)]
        if missing:
            raise AdapterUnavailableError(
                "ERP_BACKEND=erpnext but missing required env vars: "
                + ", ".join(missing)
                + " (fail-closed: refusing to start an unconfigured production adapter)"
            )
        return cls(
            url=env["ERPNEXT_URL"],
            api_key=env["ERPNEXT_API_KEY"],
            api_secret=env["ERPNEXT_API_SECRET"],
        )

    def push_journal(self, entry: JournalEntry, account_map: Dict[str, str]) -> ErpReceipt:
        doc = {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "posting_date": entry.date.isoformat(),
            "user_remark": f"{entry.memo} [{entry.source_event_id}]".strip(),
            "accounts": [
                {
                    "account": account_map.get(l.account_code, l.account_code),
                    "debit_in_account_currency": l.debit_kobo / 100.0,
                    "credit_in_account_currency": l.credit_kobo / 100.0,
                }
                for l in entry.lines
            ],
        }
        try:
            resp = self._client.post("/api/resource/Journal Entry", json=doc)
        except httpx.HTTPError as exc:
            raise ErpPushError(f"erpnext transport error: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ErpPushError(
                f"erpnext auth failed (HTTP {resp.status_code}); check ERPNEXT_API_KEY/SECRET"
            )
        if resp.status_code >= 400:
            raise ErpPushError(f"erpnext push rejected (HTTP {resp.status_code}): {resp.text[:200]}")
        try:
            name = resp.json()["data"]["name"]
        except Exception as exc:  # noqa: BLE001
            raise ErpPushError(f"erpnext push returned malformed response: {exc}") from exc
        # Submit the document (draft -> submitted).
        try:
            sub = self._client.post(
                "/api/method/frappe.client.submit",
                json={"doc": {"doctype": "Journal Entry", "name": name}},
            )
        except httpx.HTTPError as exc:
            raise ErpPushError(f"erpnext submit transport error: {exc}") from exc
        if sub.status_code >= 400:
            raise ErpPushError(f"erpnext submit failed (HTTP {sub.status_code})")
        return ErpReceipt(
            receipt_id=f"ERPNEXT-{name}",
            backend=ErpBackend.ERPNEXT,
            external_ref=name,
            entry_hash=entry.hash or "",
            detail=f"Journal Entry {name} submitted",
        )

    def health(self) -> bool:
        try:
            resp = self._client.get("/api/method/ping")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
