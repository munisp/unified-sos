"""Shared scheme-adapter base types for mod-mobility-switch.

Fail-closed idiom (mirrors services/mod-kyc-kyb/app/adapters/base.py):
optional production dependencies raise AdapterUnavailableError unless the
adapter is explicitly enabled with a configured client/credentials.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Mapping


class AdapterUnavailableError(RuntimeError):
    """Raised when an optional production scheme adapter is not configured."""


class SchemeSignatureError(PermissionError):
    """Raised when an inbound scheme callback fails signature verification."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry/timeout policy for outbound scheme calls."""

    attempts: int = 3
    backoff_s: float = 0.25
    timeout_s: float = 10.0

    def sleep_for(self, attempt: int) -> None:  # pragma: no cover - timing only
        time.sleep(self.backoff_s * (2 ** max(0, attempt - 1)))


DEFAULT_RETRY_POLICY = RetryPolicy()


def new_request_id() -> str:
    """Mint a correlation/request ID for outbound scheme calls."""
    return f"req-{uuid.uuid4().hex[:16]}"


def propagate_request_id(headers: Mapping[str, str] | None) -> str:
    """Return the inbound request ID to propagate, or mint a new one.

    Accepts X-Request-ID / X-Correlation-ID (case-insensitive lookup).
    """
    if headers:
        for key, value in headers.items():
            if key.lower() in ("x-request-id", "x-correlation-id") and value:
                return value
    return new_request_id()


@dataclass
class RequestContext:
    """Per-call context carried across scheme adapter invocations."""

    request_id: str = field(default_factory=new_request_id)

    @classmethod
    def from_headers(cls, headers: Mapping[str, str] | None) -> "RequestContext":
        return cls(request_id=propagate_request_id(headers))
