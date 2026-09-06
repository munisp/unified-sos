"""Base types for citizen channel adapters (USSD / IVR)."""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production channel dependency is missing."""


class ChannelResponse(BaseModel):
    """One deterministic channel turn: text to render + session control."""

    text: str
    end_session: bool = False


class CitizenChannelAdapter(Protocol):
    """Deterministic session turn handler for a citizen channel.

    ``msisdn_hash`` is the SHA-256 of the caller's phone number — adapters
    never see the raw MSISDN. ``session_id`` is the telco session handle used
    to resume the menu state machine across turns.
    """

    def handle_session(
        self,
        state_id: str,
        session_id: str,
        msisdn_hash: str,
        input_text: str,
    ) -> ChannelResponse: ...
