"""PII redaction primitives shared by all SOS services (Stage 7.C hygiene).

Canonical home of :func:`sanitize_error_message` (moved from
control-plane ``app/pii_guard.py``, which re-exports it for compatibility)
plus :class:`PiiRedactionFilter`, a ``logging.Filter`` that scrubs PII-looking
values from every log record. ``instrument_fastapi`` attaches the filter to
the root logger so all instrumented services get redaction by default.

Import convention (same as ``_shared.hashchain``): services insert the
``services/`` directory into ``sys.path`` before importing.
"""
from __future__ import annotations

import logging
import re

#: Field-name fragments that indicate citizen PII (case-insensitive, matched
#: against normalized keys/text).
PII_FIELD_PATTERNS: tuple[str, ...] = (
    "nin", "bvn", "passport", "biometric", "fingerprint",
    "firstname", "lastname", "middlename", "fullname",
    "dateofbirth", "dob", "phonenumber", "msisdn", "emailaddress",
    "homeaddress", "residentialaddress", "streetaddress",
    "mothersmaiden", "nextofkin", "nationalid", "voterscard", "driverslicense",
)

#: In-string variants used to scrub operator/log messages (defence in depth:
#: downstream systems may echo request payloads containing PII into errors).
_NIN_BVN_IN_TEXT = re.compile(r"\b\d{11}\b")
_PHONE_IN_TEXT = re.compile(r"\b(?:\+?234|0)\d{10}\b")


def sanitize_error_message(message: str, max_len: int = 300) -> str:
    """Scrub PII-looking values from an error/log message.

    Redacts 11-digit NIN/BVN values, Nigerian MSISDNs, and
    ``field=value``-style fragments whose field name looks like PII.
    """
    text = _NIN_BVN_IN_TEXT.sub("[REDACTED-11D]", message)
    text = _PHONE_IN_TEXT.sub("[REDACTED-PHONE]", text)
    lowered = text.lower()
    for pattern in PII_FIELD_PATTERNS:
        idx = lowered.find(pattern)
        while idx != -1:
            m = re.match(
                re.escape(pattern) + r"\s*[:=]\s*\S+", text[idx:], re.IGNORECASE
            )
            if m:
                text = text[:idx] + pattern + "=[REDACTED]" + text[idx + m.end():]
                lowered = text.lower()
            idx = lowered.find(pattern, idx + len(pattern))
    return text[:max_len]


class PiiRedactionFilter(logging.Filter):
    """Logging filter that sanitizes record messages in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.msg = sanitize_error_message(str(record.msg) % tuple(
                    str(a) for a in record.args
                ))
                record.args = ()
            else:
                record.msg = sanitize_error_message(str(record.msg))
        except Exception:
            pass  # never let redaction break logging itself
        return True


def attach_pii_redaction(logger: logging.Logger | None = None) -> PiiRedactionFilter:
    """Attach the redaction filter to ``logger`` (root by default); idempotent."""
    logger = logger or logging.getLogger()
    for f in logger.filters:
        # Class-name check: the module may be imported under both
        # `_shared.pii_guard` and top-level `pii_guard` names.
        if isinstance(f, PiiRedactionFilter) or type(f).__name__ == "PiiRedactionFilter":
            return f
    filt = PiiRedactionFilter()
    logger.addFilter(filt)
    return filt
