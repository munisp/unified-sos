"""USSD channel adapter — deterministic menu state machine over the catalog.

Menu tree (max depth 4):
    root     → numbered service categories (from the state catalog)
    category → numbered services within the chosen category
    service  → confirm screen (1 = submit, 0 = back to root)
    confirm  → submits via CitizenPortalService.submit_service_request

Session store lives in the repository (hashed MSISDN only — the raw number
never reaches this layer). Sessions expire after 180s of inactivity; an
expired or unknown session restarts at the root menu.

The caller is auto-provisioned a channel-bound identity wallet keyed by the
hashed MSISDN (``nin_hash`` carries the MSISDN hash — hash-only, no raw
identifier); settlement is skipped for channel wallets in this reference
implementation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from ..domain import (
    ChannelKind,
    ChannelSession,
    IdentityWallet,
    Priority,
    ServiceCatalogEntry,
    ServiceCategory,
)
from ..service import CitizenPortalService, NotFoundError, TenantIsolationError
from .base import ChannelResponse

SESSION_TIMEOUT = timedelta(seconds=180)
MAX_DEPTH = 4

NODE_ROOT = "root"
NODE_CATEGORY = "category"
NODE_CONFIRM = "confirm"

PROMPTS = {
    "welcome": "Welcome to {state} e-Government services.",
    "pick_category": "Select a service category:",
    "pick_service": "Select a service:",
    "confirm": "Confirm request:\n{name}\nFee: NGN {fee}\n1. Confirm\n0. Main menu",
    "submitted": "Request submitted.\nReference: {request_id}\nYou will receive an SMS update.",
    "invalid": "Invalid selection. Please try again.",
    "expired": "Session expired. Dial again to restart.",
    "unavailable": "Service temporarily unavailable. Please try later.",
}


def _fmt_fee(kobo: int) -> str:
    return f"{kobo / 100:,.2f}"


class UssdChannelAdapter:
    """USSD state machine; also the shared core reused by the IVR adapter."""

    channel_kind = ChannelKind.USSD

    def __init__(self, service: CitizenPortalService, prompts: Optional[dict] = None) -> None:
        self.service = service
        self.repo = service.repo
        self.prompts = dict(PROMPTS if prompts is None else prompts)

    # -- session lifecycle --------------------------------------------------
    def _load_session(self, state_id: str, session_id: str, msisdn_hash: str, now: datetime) -> ChannelSession:
        session = self.repo.get_channel_session(session_id)
        if session is not None:
            if session.state_id != state_id or session.msisdn_hash != msisdn_hash:
                # Fail-closed: never continue a session across tenants/callers.
                self.repo.delete_channel_session(session_id)
                raise TenantIsolationError("channel session belongs to another tenant or caller")
            if now - session.last_activity > SESSION_TIMEOUT:
                self.repo.delete_channel_session(session_id)
                session = None
        if session is None:
            session = ChannelSession(
                session_id=session_id,
                state_id=state_id,
                msisdn_hash=msisdn_hash,
                channel=self.channel_kind,
            )
        session.last_activity = now
        return session

    # -- menu rendering -------------------------------------------------------
    def _catalog(self, state_id: str) -> List[ServiceCatalogEntry]:
        entries = [e for e in self.service.ensure_catalog(state_id) if e.active]
        return sorted(entries, key=lambda e: e.service_code)

    def _categories(self, entries: List[ServiceCatalogEntry]) -> List[ServiceCategory]:
        seen: List[ServiceCategory] = []
        for e in entries:
            if e.category not in seen:
                seen.append(e.category)
        return seen

    def _root_menu(self, state_id: str, prefix: str = "") -> str:
        categories = self._categories(self._catalog(state_id))
        lines = [self.prompts["welcome"].format(state=state_id.title()), self.prompts["pick_category"]]
        lines += [f"{i}. {c.value.title()}" for i, c in enumerate(categories, 1)]
        return (prefix + "\n" if prefix else "") + "\n".join(lines)

    # -- turn handler ---------------------------------------------------------
    def handle_session(
        self,
        state_id: str,
        session_id: str,
        msisdn_hash: str,
        input_text: str,
    ) -> ChannelResponse:
        now = datetime.now(timezone.utc)
        try:
            session = self._load_session(state_id, session_id, msisdn_hash, now)
        except TenantIsolationError:
            return ChannelResponse(text=self.prompts["unavailable"], end_session=True)

        text = (input_text or "").strip()
        entries = self._catalog(state_id)

        if session.node == NODE_ROOT:
            if not text:
                self.repo.save_channel_session(session)
                return ChannelResponse(text=self._root_menu(state_id))
            categories = self._categories(entries)
            choice = self._parse_choice(text, len(categories))
            if choice is None:
                self.repo.save_channel_session(session)
                return ChannelResponse(text=self._root_menu(state_id, prefix=self.prompts["invalid"]))
            session.node = NODE_CATEGORY
            session.depth = 1
            session.selections = [categories[choice - 1].value]
            self.repo.save_channel_session(session)
            return ChannelResponse(text=self._category_menu(session, entries))

        if session.node == NODE_CATEGORY:
            if text == "0":  # back to main menu
                session.node = NODE_ROOT
                session.depth = 0
                session.selections = []
                self.repo.save_channel_session(session)
                return ChannelResponse(text=self._root_menu(state_id))
            services = self._services_in(session, entries)
            choice = self._parse_choice(text, len(services))
            if choice is None:
                self.repo.save_channel_session(session)
                return ChannelResponse(
                    text=self.prompts["invalid"] + "\n" + self._category_menu(session, entries)
                )
            entry = services[choice - 1]
            session.node = NODE_CONFIRM
            session.depth = 2
            session.selections = session.selections[:1] + [entry.service_code]
            self.repo.save_channel_session(session)
            return ChannelResponse(text=self._confirm_menu(entry))

        if session.node == NODE_CONFIRM:
            if text == "0":
                session.node = NODE_ROOT
                session.depth = 0
                session.selections = []
                self.repo.save_channel_session(session)
                return ChannelResponse(text=self._root_menu(state_id))
            if text != "1":
                entry = self._selected_entry(session, entries)
                self.repo.save_channel_session(session)
                body = self._confirm_menu(entry) if entry else self._root_menu(state_id)
                return ChannelResponse(text=self.prompts["invalid"] + "\n" + body)
            response = self._submit(session)
            self.repo.delete_channel_session(session.session_id)
            return response

        # Unknown node (depth guard) — fail-closed restart at root.
        session.node = NODE_ROOT
        session.depth = 0
        session.selections = []
        self.repo.save_channel_session(session)
        return ChannelResponse(text=self._root_menu(state_id))

    # -- helpers ----------------------------------------------------------------
    @staticmethod
    def _parse_choice(text: str, upper: int) -> Optional[int]:
        if not text.isdigit():
            return None
        choice = int(text)
        return choice if 1 <= choice <= upper else None

    def _services_in(self, session: ChannelSession, entries: List[ServiceCatalogEntry]) -> List[ServiceCatalogEntry]:
        if not session.selections:
            return []
        return [e for e in entries if e.category.value == session.selections[0]]

    def _selected_entry(self, session: ChannelSession, entries: List[ServiceCatalogEntry]) -> Optional[ServiceCatalogEntry]:
        if len(session.selections) < 2:
            return None
        for e in entries:
            if e.service_code == session.selections[1]:
                return e
        return None

    def _category_menu(self, session: ChannelSession, entries: List[ServiceCatalogEntry]) -> str:
        services = self._services_in(session, entries)
        lines = [self.prompts["pick_service"]]
        lines += [f"{i}. {s.name}" for i, s in enumerate(services, 1)]
        return "\n".join(lines)

    def _confirm_menu(self, entry: ServiceCatalogEntry) -> str:
        return self.prompts["confirm"].format(name=entry.name, fee=_fmt_fee(entry.base_fee_kobo))

    def _channel_wallet_id(self, session: ChannelSession) -> str:
        return f"CHW-{session.state_id}-{session.msisdn_hash[:16]}"

    def _submit(self, session: ChannelSession) -> ChannelResponse:
        wallet_id = self._channel_wallet_id(session)
        if self.repo.get_wallet(wallet_id) is None:
            self.repo.save_wallet(
                IdentityWallet(
                    wallet_id=wallet_id,
                    state_id=session.state_id,
                    nin_hash=session.msisdn_hash,  # channel-bound identity: hash-only
                    keycloak_realm=f"sos-{session.state_id}",
                )
            )
        try:
            request = self.service.submit_service_request(
                session.state_id,
                wallet_id,
                session.selections[1],
                {"channel": session.channel.value, "session_id": session.session_id},
                Priority.STANDARD,
            )
        except (NotFoundError, TenantIsolationError, IndexError):
            return ChannelResponse(text=self.prompts["unavailable"], end_session=True)
        return ChannelResponse(
            text=self.prompts["submitted"].format(request_id=request.request_id),
            end_session=True,
        )
