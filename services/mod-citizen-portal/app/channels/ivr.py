"""IVR channel adapter — DTMF grammar over the shared USSD state machine.

DTMF grammar: digits select menu entries (same numbering as USSD), ``*`` is
ignored (common telco prefix), ``#`` terminates input and is stripped. The
prompt text is a TTS template per locale; ``yo``/``ha``/``ig`` are stub
locales that reuse the English copy with a locale marker until native
translations are recorded.
"""
from __future__ import annotations

from typing import Dict, Optional

from ..domain import ChannelKind
from ..service import CitizenPortalService
from .base import ChannelResponse
from .ussd import PROMPTS, UssdChannelAdapter

SUPPORTED_LOCALES = ("en", "yo", "ha", "ig")


def _localized(prompts: Dict[str, str], locale: str) -> Dict[str, str]:
    """Stub localization: non-English locales reuse the English copy with a
    locale marker until recorded translations ship."""
    if locale == "en":
        return dict(prompts)
    return {key: f"[{locale}] {value}" for key, value in prompts.items()}


TTS_PROMPTS: Dict[str, Dict[str, str]] = {
    locale: _localized(PROMPTS, locale) for locale in SUPPORTED_LOCALES
}


class IvrChannelAdapter(UssdChannelAdapter):
    """IVR adapter: DTMF input normalized, then handled by the shared
    deterministic menu state machine with locale-specific TTS prompts."""

    channel_kind = ChannelKind.IVR

    def __init__(self, service: CitizenPortalService, locale: str = "en") -> None:
        if locale not in SUPPORTED_LOCALES:
            locale = "en"  # fail-closed to the default locale
        self.locale = locale
        super().__init__(service, prompts=TTS_PROMPTS[locale])

    @staticmethod
    def _normalize_dtmf(input_text: str) -> str:
        """Strip DTMF framing: ``*`` prefixes and the ``#`` terminator."""
        return (input_text or "").replace("*", "").replace("#", "").strip()

    def handle_session(
        self,
        state_id: str,
        session_id: str,
        msisdn_hash: str,
        input_text: str,
    ) -> ChannelResponse:
        return super().handle_session(
            state_id, session_id, msisdn_hash, self._normalize_dtmf(input_text)
        )

    def tts_prompt(self, key: str, **kwargs: str) -> str:
        """Render a TTS prompt template for the adapter locale."""
        return self.prompts[key].format(**kwargs)
