"""SOS Edge Daemon — offline-first reference implementation.

Python reference implementation of the SOS edge protocol (production target:
Rust daemon on ruggedized Android POS terminals — see edge/README.md).

Components:
- ``models``  — ticket/waybill payload models aligned with contracts/asyncapi
- ``crypto``  — Ed25519 signing (stand-in for the hardware secure element)
- ``outbox``  — crash-safe SQLite outbox (WAL) with monotonic sequencing
- ``sync``    — batch sync engine with backoff/jitter and idempotent dedupe keys
- ``gateway`` — fake in-process gateway used by the test-suite
"""

__version__ = "0.1.0"
