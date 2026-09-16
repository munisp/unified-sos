"""Tests for _shared.pii_guard: message sanitization + logging filter wiring."""
from __future__ import annotations

import logging

from pii_guard import (
    PiiRedactionFilter,
    attach_pii_redaction,
    sanitize_error_message,
)


def test_sanitize_redacts_nin_bvn_values():
    assert "12345678901" not in sanitize_error_message("bad nin 12345678901 in payload")
    assert "[REDACTED-11D]" in sanitize_error_message("bad nin 12345678901 in payload")


def test_sanitize_redacts_phone_values():
    out = sanitize_error_message("callback failed for +2348012345678")
    assert "+2348012345678" not in out
    assert "[REDACTED-PHONE]" in out


def test_sanitize_redacts_field_value_fragments():
    out = sanitize_error_message("validation failed: nin=12345678901 msisdn=2348012345678")
    assert "12345678901" not in out
    assert "nin=[redacted]" in out.lower()
    assert "msisdn=[redacted]" in out.lower()


def test_sanitize_truncates_and_keeps_benign_text():
    assert sanitize_error_message("plain error") == "plain error"
    assert len(sanitize_error_message("x" * 1000)) == 300


def test_redaction_filter_scrubs_log_records(caplog):
    logger = logging.getLogger("sos-test-pii")
    logger.addFilter(PiiRedactionFilter())
    try:
        with caplog.at_level(logging.INFO, logger="sos-test-pii"):
            logger.info("verify failed for nin 12345678901")
    finally:
        logger.filters.clear()
    assert "12345678901" not in caplog.text
    assert "[REDACTED-11D]" in caplog.text


def test_instrument_fastapi_attaches_redaction():
    from fastapi import FastAPI

    import observability as _obs
    instrument_fastapi = _obs.instrument_fastapi

    root = logging.getLogger()
    root.filters.clear()
    instrument_fastapi(FastAPI(), "pii-test-svc")
    # Compare by class name: the filter module may be imported under either
    # the `_shared.pii_guard` or top-level `pii_guard` name.
    names = [type(f).__name__ for f in root.filters]
    assert "PiiRedactionFilter" in names
    # Idempotent: a second instrument call must not stack filters.
    instrument_fastapi(FastAPI(), "pii-test-svc-2")
    names = [type(f).__name__ for f in root.filters]
    assert names.count("PiiRedactionFilter") == 1
    root.filters.clear()


def test_attach_pii_redaction_idempotent():
    logger = logging.getLogger("sos-test-pii-2")
    f1 = attach_pii_redaction(logger)
    f2 = attach_pii_redaction(logger)
    assert f1 is f2
    logger.filters.clear()
