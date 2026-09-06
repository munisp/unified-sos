"""Telco gateway seam (Africa's Talking / generic) — optional, fail-closed.

Production terminates USSD/IVR sessions at a telco gateway (Africa's Talking
or a generic aggregator) which webhooks each turn to
``POST /channels/{ussd,ivr}/callback``. This seam loads the optional client
SDK for outbound SMS notifications; the package is an optional dependency,
so any import/connection failure raises :class:`AdapterUnavailableError`
rather than breaking the inbound webhook path (which needs no SDK).
"""
from __future__ import annotations

from typing import Protocol

from .base import AdapterUnavailableError


class TelcoGateway(Protocol):
    def send_sms(self, msisdn_hash: str, text: str) -> None: ...


class NullTelcoGateway:
    """Deterministic no-op gateway for tests and local runs."""

    def __init__(self) -> None:
        self.sent: list = []

    def send_sms(self, msisdn_hash: str, text: str) -> None:
        self.sent.append((msisdn_hash, text))


def load_africastalking_gateway(username: str, api_key: str) -> TelcoGateway:
    """Load the optional Africa's Talking SDK; fail-closed when absent."""
    try:
        import africastalking  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AdapterUnavailableError(
            "africastalking SDK not installed; outbound SMS disabled (fail-closed)"
        ) from exc

    africastalking.initialize(username, api_key)
    sms = africastalking.SMS

    class _ATGateway:
        def send_sms(self, msisdn_hash: str, text: str) -> None:
            # The gateway resolves the raw MSISDN telco-side; we pass hashes only.
            sms.send(text, [msisdn_hash])

    return _ATGateway()


def verify_shared_secret(provided: str, expected: str) -> bool:
    """Constant-time shared-secret comparison for telco webhook auth."""
    import hmac

    if not provided or not expected:
        return False  # fail-closed: unconfigured secret rejects everything
    return hmac.compare_digest(provided, expected)
