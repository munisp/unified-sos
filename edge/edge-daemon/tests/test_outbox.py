"""Offline buffering: capacity, sequencing, crash recovery."""
import os
import time

import pytest

from edge_daemon.crypto import DeviceSigner
from edge_daemon.daemon import EdgeDaemon
from edge_daemon.models import RevenueTicketPayload
from edge_daemon.outbox import Outbox, OutboxFullError

DEVICE = "POS-TAR-GEMBU-001"


def ticket(n: int) -> RevenueTicketPayload:
    return RevenueTicketPayload(
        state_id="taraba",
        bill_reference=f"BR-2026-{n:06d}",
        payer_id=f"TRADER-{n:05d}",
        levy_code="130",
        amount_kobo=50_000,
        collector_id="AGENT-7",
    )


@pytest.fixture
def signer():
    return DeviceSigner.generate(DEVICE)


def test_offline_buffering_5000_records(tmp_path, signer):
    """WP-05 acceptance: ≥5,000 signed transactions buffered offline."""
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer)
    for i in range(5_000):
        daemon.issue(ticket(i))
    assert daemon.outbox.pending_count() == 5_000
    # sequences are strictly monotonic 1..5000
    seqs = [r.sequence for r in daemon.outbox.pending(limit=5_000)]
    assert seqs == list(range(1, 5_001))
    daemon.close()


def test_capacity_floor_enforced(tmp_path):
    with pytest.raises(ValueError):
        Outbox(tmp_path / "edge.db", DEVICE, capacity=1_000)


def test_capacity_limit_raises(tmp_path, signer):
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer, capacity=5_000)
    for i in range(5_000):
        daemon.issue(ticket(i))
    with pytest.raises(OutboxFullError):
        daemon.issue(ticket(5_001))
    daemon.close()


def test_crash_recovery_reopen_mid_queue(tmp_path, signer):
    """Kill the daemon with pending records, reopen, keep going — no sequence reuse."""
    db = tmp_path / "edge.db"
    daemon = EdgeDaemon(db, DEVICE, signer=signer)
    for i in range(120):
        daemon.issue(ticket(i))
    daemon.close()  # simulate crash/power loss after records persisted

    resumed = EdgeDaemon.reopen(db, DEVICE, signer=signer)
    assert resumed.outbox.pending_count() == 120
    assert resumed.outbox.next_sequence() == 121
    rec = resumed.issue(ticket(120))
    assert rec.sequence == 121  # no reuse after crash
    assert resumed.outbox.pending_count() == 121
    resumed.close()

    # second reopen still sees everything
    again = EdgeDaemon.reopen(db, DEVICE, signer=signer)
    assert again.outbox.pending_count() == 121
    again.close()


def test_wal_journal_and_db_files(tmp_path, signer):
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer)
    mode = daemon.outbox._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    daemon.close()


def test_buffer_5000_throughput_sanity(tmp_path, signer):
    """Buffering 5k signed records must be fast enough for POS use (<30s)."""
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer)
    start = time.perf_counter()
    for i in range(5_000):
        daemon.issue(ticket(i))
    elapsed = time.perf_counter() - start
    assert elapsed < 30.0, f"buffering 5000 records took {elapsed:.1f}s"
    daemon.close()
