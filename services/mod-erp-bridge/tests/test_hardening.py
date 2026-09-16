"""Hardening tests: WAT journal dating, timestamp rejection, persistent
processed-event dedupe across restart."""
from __future__ import annotations

from datetime import date

import pytest

from app.adapters import AdapterUnavailableError, FixtureErpAdapter
from app.dedupe import (
    FileProcessedEventStore,
    InMemoryProcessedEventStore,
    PostgresProcessedEventStore,
    select_processed_event_store,
)
from app.domain import JournalEntry, JournalLine, PushStatus
from app.service import (
    ErpBridgeService,
    InvalidSettlementEventError,
    journal_date_from_timestamp,
)

TENANT = "lagos"


def make_service(**kwargs) -> ErpBridgeService:
    kwargs.setdefault("backoff_base_s", 0.0)
    kwargs.setdefault("adapter", FixtureErpAdapter())
    return ErpBridgeService(**kwargs)


def settlement_payload(ts=None, ref="BILL-1", amount=125000):
    payload = {
        "state_id": TENANT,
        "bill_reference": ref,
        "splits": [
            {"beneficiary": "STATE_CONSOLIDATED_REVENUE_FUND",
             "amount_kobo": amount},
        ],
    }
    if ts is not None:
        payload["timestamp"] = ts
    return payload


# --- C7: WAT timezone journal dating ------------------------------------------

def test_wat_month_boundary_utc_late_evening() -> None:
    # 2026-03-31T23:30Z is 2026-04-01 00:30 in Africa/Lagos (UTC+1).
    svc = make_service()
    rec = svc.ingest_settlement(settlement_payload("2026-03-31T23:30:00Z"))
    assert rec.status == PushStatus.PUSHED
    journal = svc.get_journal(TENANT, rec.entry_id)
    assert journal.date == date(2026, 4, 1)


def test_wat_same_day_before_midnight_wat() -> None:
    svc = make_service()
    rec = svc.ingest_settlement(settlement_payload("2026-03-31T22:59:59Z"))
    assert svc.get_journal(TENANT, rec.entry_id).date == date(2026, 3, 31)


def test_wat_offset_timestamp_respected() -> None:
    svc = make_service()
    rec = svc.ingest_settlement(
        settlement_payload("2026-03-31T23:30:00+00:00", ref="BILL-off"))
    assert svc.get_journal(TENANT, rec.entry_id).date == date(2026, 4, 1)


def test_journal_date_from_timestamp_helper() -> None:
    assert journal_date_from_timestamp("2026-03-31T23:30:00Z") == date(2026, 4, 1)
    assert journal_date_from_timestamp("2026-01-01T00:30:00+01:00") == date(2026, 1, 1)


def test_missing_timestamp_rejected() -> None:
    svc = make_service()
    with pytest.raises(InvalidSettlementEventError):
        svc.ingest_settlement(settlement_payload(ts=None))
    with pytest.raises(InvalidSettlementEventError):
        svc.ingest_settlement(settlement_payload(ts=""))
    assert svc.list_journals(TENANT) == []  # nothing silently booked


def test_unparseable_timestamp_rejected() -> None:
    svc = make_service()
    with pytest.raises(InvalidSettlementEventError):
        svc.ingest_settlement(settlement_payload(ts="not-a-date"))
    with pytest.raises(InvalidSettlementEventError):
        svc.ingest_settlement(settlement_payload(ts="2026-03-31"))  # date only
    with pytest.raises(InvalidSettlementEventError):
        svc.ingest_settlement(settlement_payload(ts="2026-03-31T23:30:00"))  # naive
    assert svc.list_journals(TENANT) == []


# --- C8: persistent processed-event store ---------------------------------------

def test_file_store_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "dedupe.json"
    store = FileProcessedEventStore(path)
    store.mark_processed(TENANT, "settlement:BILL-9", "SETTLE-BILL-9")
    # "Restart": brand-new instance over the same file.
    reloaded = FileProcessedEventStore(path)
    assert reloaded.lookup(TENANT, "settlement:BILL-9") == "SETTLE-BILL-9"
    assert reloaded.lookup(TENANT, "settlement:OTHER") is None


def test_consumer_dedupe_survives_restart(tmp_path) -> None:
    path = tmp_path / "dedupe.json"
    adapter1 = FixtureErpAdapter()
    svc1 = make_service(event_store=FileProcessedEventStore(path))
    svc1.adapter = adapter1
    rec1 = svc1.ingest_settlement(settlement_payload("2026-03-31T23:30:00Z"))
    assert rec1.status == PushStatus.PUSHED
    pushes_before = len(adapter1.pushed) if hasattr(adapter1, "pushed") else None

    # Restart: new service, same dedupe file, fresh adapter.
    adapter2 = FixtureErpAdapter()
    svc2 = make_service(adapter=adapter2,
                        event_store=FileProcessedEventStore(path))
    rec2 = svc2.ingest_settlement(settlement_payload("2026-03-31T23:30:00Z"))
    assert rec2.status == PushStatus.DEDUPED  # replayed, not double-posted
    if hasattr(adapter2, "pushed"):
        assert len(adapter2.pushed) == 0  # adapter never saw the replay


def test_processed_marker_same_unit_of_work_as_journal(tmp_path) -> None:
    path = tmp_path / "dedupe.json"
    store = FileProcessedEventStore(path)
    svc = make_service(event_store=store)
    entry = JournalEntry(
        entry_id="JE-UOW", tenant_state_id=TENANT, date=date(2026, 4, 1),
        memo="uow",
        lines=[
            JournalLine(account_code="1000_CASH_TREASURY", debit_kobo=100),
            JournalLine(account_code="3000_CRF", credit_kobo=100),
        ],
        source_event_id="evt-uow",
    )
    svc.ingest(entry)
    # Journal row and processed marker are both present.
    assert svc.get_journal(TENANT, "JE-UOW").entry_id == "JE-UOW"
    assert FileProcessedEventStore(path).lookup(TENANT, "evt-uow") == "JE-UOW"


def test_dedupe_replay_via_store_when_memory_empty(tmp_path) -> None:
    path = tmp_path / "dedupe.json"
    svc = make_service(event_store=FileProcessedEventStore(path))
    svc.ingest_settlement(settlement_payload("2026-03-01T10:00:00Z", ref="BILL-5"))
    # Simulate restart: wipe in-memory indexes but keep the file store.
    svc2 = make_service(event_store=FileProcessedEventStore(path))
    assert svc2._by_source[TENANT] == {}
    rec = svc2.ingest_settlement(settlement_payload("2026-03-01T10:00:00Z", ref="BILL-5"))
    assert rec.status == PushStatus.DEDUPED


def test_select_store_fail_closed_production() -> None:
    with pytest.raises(AdapterUnavailableError):
        select_processed_event_store(env={"SOS_PROFILE": "production"})
    store = select_processed_event_store(env={
        "SOS_PROFILE": "production", "SOS_ERP_DEDUPE_FILE": "/tmp/x-dedupe.json"})
    assert isinstance(store, FileProcessedEventStore)


def test_select_store_dev_default_and_file(tmp_path) -> None:
    assert isinstance(select_processed_event_store(env={}), InMemoryProcessedEventStore)
    store = select_processed_event_store(
        env={"SOS_ERP_DEDUPE_FILE": str(tmp_path / "d.json")})
    assert isinstance(store, FileProcessedEventStore)


def test_postgres_store_fail_closed_without_dsn() -> None:
    with pytest.raises(AdapterUnavailableError):
        PostgresProcessedEventStore(dsn=None)


def test_build_service_wires_dedupe_file(tmp_path) -> None:
    from app.service import build_service

    svc = build_service(env={"SOS_ERP_DEDUPE_FILE": str(tmp_path / "d.json")})
    assert isinstance(svc._events, FileProcessedEventStore)
    with pytest.raises(AdapterUnavailableError):
        build_service(env={"SOS_PROFILE": "production"})
