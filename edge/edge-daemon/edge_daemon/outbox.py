"""Crash-safe SQLite outbox.

Design notes (mirrors the Rust production daemon):

- **WAL journal mode** — a crash mid-write cannot corrupt committed records,
  and readers never block writers.
- **Monotonic sequence** — allocated inside an IMMEDIATE transaction as
  ``MAX(sequence) + 1`` per device; reopening the DB after a crash resumes
  the sequence without reuse (dedupe keys stay unique).
- **Sync state machine** — records move ``pending -> synced``; nothing is
  deleted until the gateway has durably acknowledged it, so a daemon killed
  mid-sync simply re-sends the same batch (idempotent server-side dedupe on
  ``(device_id, sequence)`` makes the retry safe).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Union

from .crypto import DeviceSigner
from .models import Payload, SignedRecord, SyncedRecordValidator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    device_id         TEXT NOT NULL,
    sequence          INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    signature         TEXT NOT NULL,
    signer_public_key TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','synced')),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (device_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox (device_id, sequence) WHERE status = 'pending';
"""


class OutboxFullError(RuntimeError):
    """Raised when the offline buffer reaches its configured capacity."""


class Outbox:
    """SQLite-backed signed-record outbox (≥5,000 record offline capacity)."""

    def __init__(
        self,
        db_path: Union[str, Path],
        device_id: str,
        capacity: int = 10_000,
    ) -> None:
        if capacity < 5_000:
            raise ValueError("capacity must be >= 5000 (WP-05 acceptance floor)")
        self.device_id = device_id
        self.capacity = capacity
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)

    # -- writes ----------------------------------------------------------

    def enqueue(self, signer: DeviceSigner, payload: Payload) -> SignedRecord:
        """Sign ``payload``, allocate the next sequence and persist atomically."""
        if signer.device_id != self.device_id:
            raise ValueError("signer device_id does not match outbox device_id")
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            pending = cur.execute(
                "SELECT COUNT(*) AS c FROM outbox WHERE device_id = ? AND status = 'pending'",
                (self.device_id,),
            ).fetchone()["c"]
            if pending >= self.capacity:
                self._conn.execute("ROLLBACK")
                raise OutboxFullError(
                    f"outbox full: {pending}/{self.capacity} pending records"
                )
            row = cur.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS seq FROM outbox WHERE device_id = ?",
                (self.device_id,),
            ).fetchone()
            sequence = int(row["seq"])
            record = SignedRecord(
                device_id=self.device_id,
                sequence=sequence,
                payload=payload,
                signature="",  # filled after computing signing bytes
                signer_public_key=signer.public_key_b64(),
            )
            record.signature = signer.sign(record.signing_bytes())
            cur.execute(
                """
                INSERT INTO outbox (device_id, sequence, kind, payload_json, signature, signer_public_key)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.device_id,
                    record.sequence,
                    record.payload.kind.value,
                    record.payload.model_dump_json(),
                    record.signature,
                    record.signer_public_key,
                ),
            )
            self._conn.execute("COMMIT")
            return record
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass  # transaction already finished
            raise

    # -- reads -----------------------------------------------------------

    def pending(self, limit: int = 500) -> List[SignedRecord]:
        """Return up to ``limit`` oldest pending records, in sequence order."""
        rows = self._conn.execute(
            """
            SELECT * FROM outbox
            WHERE device_id = ? AND status = 'pending'
            ORDER BY sequence ASC LIMIT ?
            """,
            (self.device_id, limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def pending_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) AS c FROM outbox WHERE device_id = ? AND status = 'pending'",
                (self.device_id,),
            ).fetchone()["c"]
        )

    def total_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) AS c FROM outbox WHERE device_id = ?", (self.device_id,)
            ).fetchone()["c"]
        )

    def next_sequence(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS s FROM outbox WHERE device_id = ?",
                (self.device_id,),
            ).fetchone()["s"]
        )

    def get(self, sequence: int) -> SignedRecord | None:
        row = self._conn.execute(
            "SELECT * FROM outbox WHERE device_id = ? AND sequence = ?",
            (self.device_id, sequence),
        ).fetchone()
        return self._row_to_record(row) if row else None

    # -- sync bookkeeping --------------------------------------------------

    def mark_synced(self, sequences: List[int]) -> int:
        """Mark records as durably acknowledged by the gateway."""
        with self._conn:
            cur = self._conn.executemany(
                "UPDATE outbox SET status = 'synced' WHERE device_id = ? AND status = 'pending' AND sequence = ?",
                [(self.device_id, s) for s in sequences],
            )
            return cur.rowcount

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SignedRecord:
        payload = SyncedRecordValidator(payload=row["payload_json"]).payload
        return SignedRecord(
            device_id=row["device_id"],
            sequence=row["sequence"],
            payload=payload,
            signature=row["signature"],
            signer_public_key=row["signer_public_key"],
        )
